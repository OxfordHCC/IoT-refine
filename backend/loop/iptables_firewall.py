import subprocess

def drop_outbound_ip(ip):
    subprocess.run([
        "sudo", "iptables",
        "-I", "OUTPUT",
        "-d", ip,
        "-j", "DROP",
    ])
    
def drop_inbound_ip(ip):
    subprocess.run([
        "sudo", "iptables",
        "-I", "INPUT",
        "-s", ip,
        "-j", "DROP"
    ])
def drop_ip_for_mac(ip, mac):
    subprocess.run([
        "sudo", "iptables",
        "-I", "FORWARD",
        "-d", ip,
        "-m", "mac",
        "--mac-source", mac,
        "-j", "DROP"
    ])

def persist():
    subprocess.run([
        "sudo", "dpkg-reconfigure",
        "-p", "critical",
        "iptables-persistent"])