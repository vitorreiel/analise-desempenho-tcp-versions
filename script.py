from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.cli import CLI
from time import sleep
import psutil
import csv
import json
import os
import math
import pandas as pd
import matplotlib.pyplot as plt
from flask import Flask, render_template_string, send_file
import threading
import scipy.stats as stats
import seaborn as sns
from colorama import Fore, Style

def enable_ip_forwarding(router):
    """Ativa o encaminhamento de pacotes IPv4 e IPv6 em um roteador."""
    router.cmd("sysctl -w net.ipv4.ip_forward=1")
    router.cmd("sysctl -w net.ipv6.conf.all.forwarding=1")

def create_topology(bw, loss, delay):
    """Create a simple Mininet topology with 2 routers and 2 hosts."""
    net = Mininet(link=TCLink)

    print("Creating network topology...")
    
    # Add routers
    r1 = net.addHost("r1", ip="10.0.1.1/24")
    r2 = net.addHost("r2", ip="10.0.2.1/24")

    # Add hosts with IPv4 configuration
    h1 = net.addHost("h1", ip="10.0.1.2/24", defaultRoute="via 10.0.1.1")
    h2 = net.addHost("h2", ip="10.0.2.2/24", defaultRoute="via 10.0.2.1")

    # Link hosts to routers
    net.addLink(h1, r1, loss=0, delay='0ms') 
    net.addLink(h2, r2, loss=0, delay='0ms') 

    # Link routers
    net.addLink(r1, r2, bw=bw, loss=loss, delay=f'{delay}ms', intfName1="r1-eth1", intfName2="r2-eth1")

    r1.setIP("192.168.1.1/30", intf="r1-eth1")
    r2.setIP("192.168.1.2/30", intf="r2-eth1")

    r1.cmd("ip -6 addr add 2001:db8:1::1/64 dev r1-eth1")
    r2.cmd("ip -6 addr add 2001:db8:1::2/64 dev r2-eth1")

    h1.cmd("ip -6 addr add 2001:db8:0:1::2/64 dev h1-eth0")
    h2.cmd("ip -6 addr add 2001:db8:0:2::2/64 dev h2-eth0")
    h1.cmd("ip -6 route add default via 2001:db8:0:1::1")
    h2.cmd("ip -6 route add default via 2001:db8:0:2::1")

    r1.cmd("ip -6 addr add 2001:db8:0:1::1/64 dev r1-eth0")
    r2.cmd("ip -6 addr add 2001:db8:0:2::1/64 dev r2-eth0")

    net.start()

    enable_ip_forwarding(r1)
    enable_ip_forwarding(r2)

    r1.cmd("ip route add 10.0.2.0/24 via 192.168.1.2")
    r2.cmd("ip route add 10.0.1.0/24 via 192.168.1.1")

    # Configuração de rotas IPv6 nos roteadores
    r1.cmd("ip -6 route add 2001:db8:0:2::/64 via 2001:db8:1::2")
    r2.cmd("ip -6 route add 2001:db8:0:1::/64 via 2001:db8:1::1")

    return net, h1, h2

def calculate_rtt_variance(rtt_values):
    """Calculate RTT Variance based on a list of RTT values."""
    if not rtt_values:
        return 0
    mean_rtt = sum(rtt_values) / len(rtt_values)  # Calculate the mean RTT
    # Calculate the variance
    variance = sum((rtt - mean_rtt) ** 2 for rtt in rtt_values) / len(rtt_values)
    return round(variance, 2)  # Return variance rounded to 2 decimal places

def configure_tcp_version(host, tcp_version):
    """Configure TCP version for the given host."""
    host.cmd(f"sysctl -w net.ipv4.tcp_congestion_control={tcp_version}")

def measure_metrics(net, h1, h2, output_csv, output_log, test_id, tcp_version, ip_version):
    """Measure TCP performance metrics and save them to a CSV file and a log file."""
    print(f"Starting TCP performance tests for {tcp_version} with {ip_version}...")

    # Configure TCP version and IP version
    configure_tcp_version(h1, tcp_version)
    configure_tcp_version(h2, tcp_version)

    # Start iperf server on h2
    if ip_version == "IPv6":
        h2.cmd("iperf3 -s -6 -p 5202 &")  # Start server with IPv6
    else:
        h2.cmd("iperf3 -s -p 5201 &")  # Start server with IPv4
    sleep(2)  # Give the server time to start

    metrics = []

    # Open log file for appending
    os.makedirs("output", exist_ok=True)  # Ensure the output directory exists
    with open(output_log, 'a') as log_file:
        # Run iperf test from h1 to h2
        print("Running iperf test...")
        log_file.write(f"Running iperf test for {tcp_version} with {ip_version}...\n")

        # Capture CPU usage before the test
        cpu_usage_before = psutil.cpu_percent(interval=1)

        if ip_version == "IPv6":
            # Use the fixed IPv6 address of h2 for iperf test
            iperf_result = h1.cmd(f"iperf3 -c 2001:db8:0:2::2%h1-eth0 -6 -p 5202 -t 3 -J")  # IPv6 test
        else:
            iperf_result = h1.cmd(f"iperf3 -c {h2.IP()} -p 5201 -t 3 -J")  # IPv4 test

        # Capture CPU usage after the test
        cpu_usage_after = psutil.cpu_percent(interval=1)

        # Average CPU usage during the test
        avg_cpu_usage = round((cpu_usage_before + cpu_usage_after) / 2, 2)

        # Write full output to log file
        log_file.write(iperf_result)
        log_file.write("\n")

        # Parse results
        try:
            iperf_data = json.loads(iperf_result)

            # Verify and extract the relevant metrics
            throughput_bps = iperf_data['end']['sum_received']['bits_per_second']
            throughput_gbps = round(throughput_bps / 1e9, 2)

            retransmissions = iperf_data['end']['sum_sent']['retransmits']
            recovery_time_total = round(iperf_data['end']['sum_sent']['seconds'], 2)
            mean_rtt = iperf_data['end']['streams'][0]['sender'].get('mean_rtt', 0)

            # Extract RTTs for variance calculation
            rtt_values = [stream['rtt'] for interval in iperf_data['intervals'] for stream in interval['streams'] if 'rtt' in stream]
            rtt_variance = calculate_rtt_variance(rtt_values)

            # Total Packets Sent (rounded)
            total_bytes_sent = iperf_data['end']['sum_sent']['bytes']
            tcp_mss = iperf_data['start']['tcp_mss_default']
            total_packets_sent = round(total_bytes_sent / tcp_mss, 2)

            packet_loss = "{:.2f}".format((retransmissions / total_packets_sent) * 100 if total_packets_sent > 0 else 0)
            max_bandwidth = bw * 1e6  # Dynamic max bandwidth based on user input (converted to bps)
            bandwidth_efficiency = round((throughput_bps / max_bandwidth) * 100, 2)

            max_rtt = iperf_data['end']['streams'][0]['sender'].get('max_rtt', 0)
            max_cwnd = iperf_data['end']['streams'][0]['sender'].get('max_snd_cwnd', 0)

            cpu_sender = round(iperf_data['end']['cpu_utilization_percent']['host_total'], 2)
            cpu_receiver = round(iperf_data['end']['cpu_utilization_percent']['remote_total'], 2)

            # Append metrics with ID to identify the test run
            metrics.append({
                'ID': test_id,
                'TCP Version': tcp_version,
                'IP Version': ip_version,
                'Throughput (Gbps)': throughput_gbps,
                'Packet Loss (%)': packet_loss,
                'Total Recovery Time (s)': recovery_time_total,
                'Mean RTT (ms)': mean_rtt,
                'RTT Variance (ms)': rtt_variance,
                'Maximum RTT (ms)': max_rtt,
                'Retransmissions': retransmissions,
                'Total Packets Sent': total_packets_sent,
                'Bandwidth Efficiency (%)': bandwidth_efficiency,
                'Max cwnd (bytes)': max_cwnd,
                'CPU Sender (%)': cpu_sender,
                'CPU Receiver (%)': cpu_receiver,
                'CPU Usage Local (%)': avg_cpu_usage
            })

        except KeyError as e:
            log_file.write(f"Error: Missing key {str(e)} in iperf result.\n")

    # Write metrics to CSV file
    os.makedirs("dataset", exist_ok=True)  # Ensure the dataset directory exists
    output_filename = f"dataset/dataset_{ip_version.lower()}_{tcp_version.lower()}.csv"
    with open(output_filename, 'a', newline='') as csvfile:  # 'a' to append data without overwriting
        fieldnames = [
            'ID', 
            'TCP Version', 
            'IP Version',
            'Throughput (Gbps)', 
            'Packet Loss (%)', 
            'Total Recovery Time (s)', 
            'Mean RTT (ms)', 
            'RTT Variance (ms)', 
            'Maximum RTT (ms)', 
            'Retransmissions', 
            'Total Packets Sent', 
            'Bandwidth Efficiency (%)', 
            'Max cwnd (bytes)', 
            'CPU Sender (%)', 
            'CPU Receiver (%)',
            'CPU Usage Local (%)'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        if csvfile.tell() == 0:  # Write header only if file is empty
            writer.writeheader()
        writer.writerows(metrics)

    print(f"Metrics saved to {output_filename}")
    print(f"Full output saved to {output_log}")

def cleanup(net):
    """Stop the Mininet network and clean up processes."""
    print("Stopping network...")
    net.stop()
    os.system("pkill -f iperf3")

# Set global style for graphs
sns.set_theme(style="whitegrid")

def generate_graphs():
    """Generate graphs from the datasets."""
    datasets_path = "dataset"
    output_path = "graficos"
    os.makedirs(output_path, exist_ok=True)  # Ensure the graphics directory exists

    files = [f for f in os.listdir(datasets_path) if f.endswith(".csv")]

    data = pd.DataFrame()
    for file in files:
        df = pd.read_csv(os.path.join(datasets_path, file))
        data = pd.concat([data, df], ignore_index=True)

    metrics = {
        "Throughput (Gbps)": "Throughput",
        "Mean RTT (ms)": "RTT",
        "Packet Loss (%)": "Packet_Loss",
        "Retransmissions": "Retransmissions",
        "CPU Usage Local (%)": "CPU_Usage"
    }

    for metric, folder_name in metrics.items():
        metric_data = data.groupby(["TCP Version", "IP Version"])[metric]
        mean_data = metric_data.mean().unstack()
        conf_interval = metric_data.apply(lambda x: stats.t.interval(0.95, len(x)-1, loc=x.mean(), scale=stats.sem(x)) if len(x) > 1 else (x.mean(), x.mean()))

        # Ensure subdirectory exists
        metric_path = os.path.join(output_path, folder_name)
        os.makedirs(metric_path, exist_ok=True)

        # Mean graph
        mean_data.plot(kind="bar", figsize=(10, 6), alpha=0.75, edgecolor="black")
        plt.title(f"Média de {metric}")
        plt.ylabel(metric)
        plt.xlabel("TCP Version")
        plt.legend(title="IP Version")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(os.path.join(metric_path, "mean.png"))
        plt.close()

        # Confidence interval graph
        mean_values = mean_data.stack()  # Flattened mean values
        conf_intervals = pd.DataFrame(conf_interval.tolist(), index=conf_interval.index, columns=["Lower", "Upper"])
        errors = conf_intervals["Upper"] - mean_values
        mean_values.unstack().plot(kind="bar", yerr=errors.unstack(), figsize=(10, 6), capsize=4, alpha=0.75, edgecolor="black")
        plt.title(f"Intervalo de Confiança de {metric}")
        plt.ylabel(metric)
        plt.xlabel("TCP Version")
        plt.legend(title="IP Version")
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.savefig(os.path.join(metric_path, "confidence_interval.png"))
        plt.close()

def serve_graphs():
    """Serve the graphs on a styled web page."""
    app = Flask(__name__)

    @app.route('/')
    def index():
        graphs_path = "graficos"
        subfolders = [f for f in os.listdir(graphs_path) if os.path.isdir(os.path.join(graphs_path, f))]
        graph_html = """
        <html>
        <head>
            <title>Gráficos de Análise e Desempenho</title>
            <style>
                body { font-family: Arial, sans-serif; background-color: #f7f7f7; margin: 0; padding: 0; }
                h1 { text-align: center; color: #333; margin-top: 20px; }
                h2 { text-align: center; color: #555; margin-top: 20px; margin-bottom: 10px; }
                .graph-section { text-align: center; margin: 30px 0; }
                img { margin: 10px; border: 2px solid #ddd; border-radius: 8px; box-shadow: 0px 4px 6px rgba(0, 0, 0, 0.1); width: 80%; }
                .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
            </style>
        </head>
        <body>
            <h1>Gráficos de Análise e Desempenho</h1>
            <div class="container">
        """
        for folder in subfolders:
            graph_html += f"<div class='graph-section'><h2>{folder.replace('_', ' ')}</h2>"
            files = os.listdir(os.path.join(graphs_path, folder))
            for file in files:
                graph_html += f'<img src="/{graphs_path}/{folder}/{file}">'
            graph_html += "</div>"
        graph_html += "</div></body></html>"
        return graph_html

    @app.route('/graficos/<folder>/<filename>')
    def serve_file(folder, filename):
        return send_file(os.path.join('graficos', folder, filename), mimetype='image/png')

    threading.Thread(target=lambda: app.run(debug=False, port=8080, use_reloader=False)).start()

if __name__ == '__main__':
    setLogLevel('info')

    # Input parameters
    bw = float(input(Fore.BLUE + "\nDigite a taxa de transferência (Gbps): " + Style.RESET_ALL)) * 1e3  # Convert Gbps to Mbps
    loss = float(input(Fore.GREEN + "Digite a porcentagem de perda de dados (%): " + Style.RESET_ALL))
    delay = float(input(Fore.RED + "Digite o atraso (ms): " + Style.RESET_ALL))
    repetitions = int(input(Fore.CYAN + "Digite a quantidade de repetições: " + Style.RESET_ALL))

    output_log = "output/full_output.log"

    # Loop through TCP versions and IP versions
    tcp_versions = ['reno', 'cubic', 'bbr', 'vegas', 'veno', 'westwood']
    ip_versions = ['IPv4', 'IPv6']

    for tcp_version in tcp_versions:
        for ip_version in ip_versions:
            for test_id in range(1, repetitions + 1):  # Repeat tests as per input
                print(f"Starting test {test_id} for TCP {tcp_version} and {ip_version}")
                # Create topology
                net, h1, h2 = create_topology(bw, loss, delay)
                try:
                    # Measure metrics
                    measure_metrics(net, h1, h2, f"dataset/dataset_{ip_version.lower()}_{tcp_version.lower()}.csv", output_log, test_id, tcp_version, ip_version)
                finally:
                    # Clean up
                    cleanup(net)

    print("Todos os testes foram executados. \n Acesse http://localhost:8080")

    # Generate graphs
    generate_graphs()

    # Serve graphs on a web page
    serve_graphs()
