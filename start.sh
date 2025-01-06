#!/bin/bash
echo -e "\033[1;32m\n- [ Verificando dependências ] ------------------------------------------------------------------------------------- \033[0m"
sudo apt update > /dev/null 2>&1 && sudo apt install mininet openvswitch-switch iperf iperf3 python3-psutil python3-pandas python3-matplotlib  python3-seaborn -y > /dev/null 2>&1
echo -e "\033[1;32m- [ Iniciando ] ---------------------------------------------------------------------------------------------------- \033[0m"
sudo python3 script.py