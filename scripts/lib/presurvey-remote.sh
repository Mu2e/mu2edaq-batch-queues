#!/bin/bash
# Read-only pre-install survey for a Slurm deployment. Writes nothing on the host.
sec() { printf '\n## %s\n' "$1"; }
sec host;      hostname; hostname -s; hostname -f 2>&1; cat /etc/redhat-release; uname -r; uptime
sec whoami;    id
sec sudo;      timeout 5 sudo -n -l 2>&1 | head -15
sec pkgs;      rpm -q openvox-agent slurm slurm-slurmctld slurm-slurmd slurm-slurmdbd slurm-perlapi munge munge-libs mariadb-server pmix hwloc chrony firewalld puppet puppet-agent ansible-core sssd epel-release nfs-utils 2>&1
sec cfgdirs;   ls -ld /etc/slurm /etc/munge /var/spool/slurm* /etc/puppetlabs /etc/ansible 2>&1
sec repos;     timeout 60 dnf -q repolist --enabled 2>&1 | head -40
sec slurm_avail; timeout 180 dnf -q --assumeno --setopt=skip_if_unavailable=True --disablerepo='*' --enablerepo='epel' --enablerepo='SLAM_EPEL_EPEL_9_x86_64' --enablerepo='osg' --enablerepo='SLAM_Open_Science_Grid_OSG_25_EL9_x86_64' --enablerepo='fermilab' --enablerepo='SLAM_Fermilab_Fermilab_Context_EL9_x86_64' --enablerepo='appstream' --enablerepo='baseos' --enablerepo='crb' list --showduplicates 'slurm' 'slurm-slurmctld' 'slurm-slurmd' 'slurm-slurmdbd' 'munge' 'pmix' 'mariadb-server' 2>&1 | head -40
sec inet;      for u in https://dl.fedoraproject.org https://repo.almalinux.org https://download.schedmd.com; do printf '%s ' "$u"; curl -s -o /dev/null -w '%{http_code}\n' --max-time 6 -I "$u" 2>&1 || echo FAIL; done
sec users;     getent passwd slurm munge mu2eshift mu2edaq root 2>&1; echo '-- local /etc/passwd entries:'; grep -E '^(slurm|munge|mu2eshift|mu2edaq):' /etc/passwd; echo '-- local /etc/group entries:'; grep -E '^(slurm|munge|mu2e|mu2e_extmon):' /etc/group; getent group slurm munge mu2e mu2e_extmon 2>&1
sec nss;       grep -E '^(passwd|group|shadow):' /etc/nsswitch.conf; for s in sssd nslcd; do printf '%s: ' $s; systemctl is-active $s 2>&1; done
sec sysuids;   awk -F: '$3<1000 {print $3":"$1}' /etc/passwd | sort -n | tr '\n' ' '; echo
sec sysgids;   awk -F: '$3<1000 {print $3":"$1}' /etc/group | sort -n | tr '\n' ' '; echo
sec homeuids;  awk -F: '$3>=1000 && $3<65534 {print $3":"$1}' /etc/passwd | sort -n | tr '\n' ' '; echo
sec homes;     ls -ld /home/* 2>&1
sec cfgmgmt;   for s in puppet pxp-agent cfengine3 salt-minion; do printf '%s: ' $s; systemctl is-active $s 2>&1; done; systemctl list-timers --all --no-pager 2>&1 | grep -iE 'puppet|ansible' ; ls /etc/cron.d 2>&1 | tr '\n' ' '; echo; grep -vE '^\s*(#|$)' /etc/puppetlabs/puppet/puppet.conf 2>&1 | head; ls -l /opt/puppetlabs/puppet/cache/state/last_run_summary.yaml 2>&1; grep -A3 -E '^(time|version|resources):' /opt/puppetlabs/puppet/cache/state/last_run_summary.yaml 2>/dev/null | grep -E 'last_run|config|total|changed|failed' | head
sec firewall;  [ "$(id -u)" = 0 ] && firewall-cmd --list-all 2>&1 | head -20; printf 'firewalld: '; systemctl is-active firewalld 2>&1; printf 'nftables: '; systemctl is-active nftables 2>&1; printf 'iptables: '; systemctl is-active iptables 2>&1
sec time;      printf 'chronyd: '; systemctl is-active chronyd 2>&1; timedatectl 2>&1 | grep -iE 'synchron|NTP|zone'; date -u
sec cgroup;    stat -fc %T /sys/fs/cgroup; cat /sys/fs/cgroup/cgroup.controllers 2>&1
sec hw;        lscpu | grep -E '^(CPU\(s\)|Model name|Socket\(s\)|Core\(s\) per socket|Thread\(s\) per core|NUMA node\(s\))'; free -m | head -2; ls /dev/nvidia* 2>/dev/null | head -3; lspci 2>/dev/null | grep -iE 'nvidia|vga|3d' | head
sec disks;     df -hT -x tmpfs -x devtmpfs -x overlay 2>&1; echo; findmnt -rn -t nfs,nfs4,cifs,lustre,gpfs,cvmfs2,fuse,autofs -o TARGET,SOURCE,FSTYPE 2>&1
sec resolve;   for n in $(seq -w 1 16); do getent hosts mu2e-mc1-worker-$n.fnal.gov || echo "NXDOMAIN mu2e-mc1-worker-$n"; done; getent hosts g2gateway01.fnal.gov g2gateway02.fnal.gov
sec shortname; getent hosts mu2e-mc1-worker-01 mu2e-mc1-worker-02 mu2e-mc1-worker-03 2>&1 || echo "short names do not resolve"; grep -E '^(search|domain)' /etc/resolv.conf
sec net;       ip -br addr 2>&1
sec listen;    ss -ltnH 2>&1 | awk '{print $4}' | sort -u | tr '\n' ' '; echo
sec services;  systemctl list-units --type=service --state=running --no-pager --no-legend 2>&1 | awk '{print $1}' | tr '\n' ' '; echo
sec load;      who 2>&1; ps -eo user:12,pcpu,pmem,etime,comm --sort=-pcpu | head -8
sec selinux;   getenforce 2>&1
sec peer_tcp22; for n in 01 02 03; do printf 'worker-%s:22 ' $n; timeout 3 bash -c "</dev/tcp/mu2e-mc1-worker-$n.fnal.gov/22" 2>/dev/null && echo open || echo closed/unreachable; done
sec sudoers;   [ "$(id -u)" = 0 ] && grep -rhvE '^\s*(#|$)' /etc/sudoers.d 2>/dev/null | head -10
