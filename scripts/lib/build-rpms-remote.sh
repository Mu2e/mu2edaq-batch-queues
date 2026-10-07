#!/bin/bash
# Build SchedMD Slurm RPMs for EL9 and EL10 in throwaway containers.
set -euo pipefail
V=${1:-26.05.4}
B=/scratch/mc1-slurm-build
mkdir -p $B/src $B/el9 $B/el10
cd $B/src
[ -f slurm-$V.tar.bz2 ] || curl -sfLO https://download.schedmd.com/slurm/slurm-$V.tar.bz2
sha256sum slurm-$V.tar.bz2 | tee $B/src/SHA256
# quay.io has no almalinux:10 tag; Docker Hub carries both.
declare -A IMAGE=([9]=quay.io/almalinux/almalinux:9 [10]=docker.io/library/almalinux:10)
for el in 9 10; do
  if ls $B/el$el/slurm-$V-*.rpm >/dev/null 2>&1; then echo "=== EL$el: RPMs for $V already built"; continue; fi
  echo "=== EL$el build start $(date -u +%T) (${IMAGE[$el]})"
  podman run --rm -v $B:/b ${IMAGE[$el]} bash -c "
    set -e
    dnf -y -q install dnf-plugins-core epel-release >/dev/null
    dnf config-manager --set-enabled crb
    dnf -y -q install rpm-build gcc make bzip2 munge-devel munge-libs pam-devel readline-devel \
      perl-ExtUtils-MakeMaker perl-devel mariadb-connector-c-devel hwloc-devel dbus-devel \
      python3 lua-devel json-c-devel libcurl-devel numactl-devel lz4-devel zlib-devel \
      autoconf automake mariadb-devel >/dev/null
    tar -xjOf /b/src/slurm-$V.tar.bz2 slurm-$V/slurm.spec > /tmp/slurm.spec
    dnf -y -q builddep /tmp/slurm.spec --define '_with_mysql --with-mysql' >/dev/null
    rpmbuild -ta /b/src/slurm-$V.tar.bz2 --define '_topdir /tmp/rpmbuild' --with mysql > /b/el$el/rpmbuild.log 2>&1 || { tail -40 /b/el$el/rpmbuild.log; exit 1; }
    cp /tmp/rpmbuild/RPMS/x86_64/*.rpm /b/el$el/
  "
  echo "=== EL$el build done $(date -u +%T)"; ls $B/el$el/*.rpm | wc -l
done
