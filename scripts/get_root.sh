#!/bin/bash
set -euo pipefail
: "${QEMU_GDB_PORT:=1234}"
: "${QEMU_SERIAL_PORT:=31337}"

usage() {
	echo "Usage: $0 <release-name>"
	exit 1
}

RELEASE_NAME="${1:-}"
if [ -z "$RELEASE_NAME" ]; then
	usage
fi

if [ ! -f "qemu_v3.sh" ]; then
	wget https://storage.googleapis.com/kernelctf-build/files/qemu_v3.sh
fi
chmod u+x qemu_v3.sh

if [ ! -f "rootfs_v3.img" ]; then
	wget https://storage.googleapis.com/kernelctf-build/files/rootfs_v3.img.gz
	gzip -d rootfs_v3.img.gz
fi

if [ ! -f "ramdisk_v1.img" ]; then
	wget https://storage.googleapis.com/kernelctf-build/files/ramdisk_v1.img
fi

if [ ! -f "flag" ]; then
	echo "kernelCTF{example_flag}" > flag
fi

if [ ! -d "core" ]; then mkdir core; fi
if [ ! -f "ramdisk_v1" ]; then unar ramdisk_v1.img; fi
pushd core >/dev/null
cpio -idmv < ../ramdisk_v1 >/dev/null 2>&1
popd >/dev/null

test_script="./core/test.sh"
if [ ! -f "$test_script" ]; then
	cat > "$test_script" <<'EOF'
#!/bin/bash
#echo run.sh running!
mount -t tmpfs -o size=100M,mode=1777 tmp /tmp
ifconfig enp0s3 10.0.2.15 netmask 255.255.255.0
ifconfig lo 127.0.0.1 netmask 255.0.0.0 up
route add default gw 10.0.2.2
HOSTNAME=NSJAIL
[[ `cat /proc/cmdline` =~ hostname=(.*) ]] && HOSTNAME="${BASH_REMATCH[1]}"
# kctf_drop_privs nsjail --chroot /chroot --config /home/user/nsjail.cfg --hostname $HOSTNAME -- /bin/bash
/bin/bash
#/bin/bash
EOF
	chmod +x "$test_script"
fi

init_file="./core/init"
marker='/busybox cp ./exp /root/chroot/bin'

if [ -f "$init_file" ] && ! grep -Fq "$marker" "$init_file"; then
	tmp_file="$(mktemp)"
	awk '
	BEGIN { inserted=0 }
	{
		if (!inserted && $0 ~ /^# Chain to real filesystem$/) {
			print "/busybox cp ./exp /root/chroot/bin"
			print "/busybox cp ./exp /root/bin/exp"
			print "/busybox cp ./test.sh /root/home/user/run.sh"
			print "/busybox chmod +x /root/home/user/run.sh"
			inserted=1
		}
		print
	}
	END {
		if (!inserted) {
			print "/busybox cp ./exp /root/chroot/bin"
			print "/busybox cp ./exp /root/bin/exp"
			print "/busybox cp ./test.sh /root/home/user/run.sh"
			print "/busybox chmod +x /root/home/user/run.sh"
		}
	}
	' "$init_file" > "$tmp_file"
	mv "$tmp_file" "$init_file"
    chmod 755 "$init_file"
fi

qemu_file="./qemu_v3.sh"
if [ -f "$qemu_file" ]; then
	tmp_file="$(mktemp)"
	sed -E \
		-e 's/,readonly//g' \
		-e 's/\<ro\>/rw/g' \
		-e 's@-initrd[[:space:]]+ramdisk_v1\.img@-initrd rootfs.cpio@g' \
		"$qemu_file" > "$tmp_file"
	mv "$tmp_file" "$qemu_file"

	if ! grep -Fq -- "nokaslr" "$qemu_file"; then
		tmp_file="$(mktemp)"
		awk '
		{
			if ($0 ~ /-append "/ && $0 !~ /nokaslr/) {
				sub(/-append "/, "-append \"nokaslr ")
			}
			print
		}
		' "$qemu_file" > "$tmp_file"
		mv "$tmp_file" "$qemu_file"
	fi

	if ! grep -Fq -- "-serial tcp:127.0.0.1:\${QEMU_SERIAL_PORT}" "$qemu_file"; then
		tmp_file="$(mktemp)"
		awk '
		BEGIN { inserted=0 }
		{
			if (!inserted && $0 ~ /-append "/) {
				print "  -serial tcp:127.0.0.1:${QEMU_SERIAL_PORT},server=on,wait=off,nodelay=on,telnet=off \\"
				inserted=1
			}
			print
		}
		' "$qemu_file" > "$tmp_file"
		mv "$tmp_file" "$qemu_file"
	fi

	if ! grep -Fq -- "-gdb tcp:127.0.0.1:\${QEMU_GDB_PORT}" "$qemu_file"; then
		tmp_file="$(mktemp)"
		awk '
		BEGIN { inserted=0 }
		{
			if (!inserted && $0 ~ /-append "/) {
				print "  -gdb tcp:127.0.0.1:${QEMU_GDB_PORT} \\"
				inserted=1
			}
			print
		}
		' "$qemu_file" > "$tmp_file"
		mv "$tmp_file" "$qemu_file"
	fi
fi

pushd core && find . | cpio -o --format=newc > ../rootfs.cpio && popd
echo "[+] qemu gdb port: ${QEMU_GDB_PORT}"
echo "[+] qemu serial port: ${QEMU_SERIAL_PORT}"
QEMU_GDB_PORT="${QEMU_GDB_PORT}" QEMU_SERIAL_PORT="${QEMU_SERIAL_PORT}" ./local_runner.sh "$RELEASE_NAME"
