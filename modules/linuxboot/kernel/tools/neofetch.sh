#!/bin/sh
# Minimal neofetch for Nikon Z30 (Wonder Lantern Linux)
CY=$(printf '\033[1;33m')   # bold yellow (Nikon accent)
CRED=$(printf '\033[1;31m') # bold red (Nikon grip stripe)
CC=$(printf '\033[1;36m')   # bold cyan
CW=$(printf '\033[1;37m')   # bold white
CG=$(printf '\033[1;32m')   # bold green
CD=$(printf '\033[0;37m')   # dim white
CR=$(printf '\033[0m')

kernel=$(uname -r)
arch=$(uname -m)
host=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0')
[ -n "$host" ] || host="Nikon Z30 (Wonder Lantern)"

up_s=$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 0)
up_m=$((up_s / 60))
up_rem=$((up_s % 60))
if [ "$up_m" -gt 0 ]; then
    uptime_str="${up_m} mins, ${up_rem} secs"
else
    uptime_str="${up_s} secs"
fi

mem_tot=$(awk '/^MemTotal:/{printf "%d", $2/1024}' /proc/meminfo)
mem_avail=$(awk '/^MemAvailable:/{printf "%d", $2/1024}' /proc/meminfo)
[ -n "$mem_avail" ] || mem_avail=$(awk '/^MemFree:/{printf "%d", $2/1024}' /proc/meminfo)
mem_used=$((mem_tot - mem_avail))

cpus=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null || echo 2)
bbver=$(busybox | head -n1 | awk '{print $2}')

if grep -q ' /mnt/sd ' /proc/mounts 2>/dev/null; then
    sd_fs=$(awk '$2=="/mnt/sd"{print $3; exit}' /proc/mounts)
    sd_df=$(df -h /mnt/sd 2>/dev/null | awk 'NR==2{print $3 " / " $2 " (" $5 ")"}')
    sd_str="${sd_df} [${sd_fs}, ro]"
elif [ -b /dev/mmcblk0 ]; then
    sd_str="/dev/mmcblk0 detected (unmounted)"
else
    sd_str="not detected"
fi

wlan_ip=$(ip -4 addr show wlan0 2>/dev/null | awk '/inet / {print $2; exit}')
if [ -n "$wlan_ip" ]; then
    wifi_str="${CG}${wlan_ip}${CR} (BCM43455)"
elif [ -d /sys/class/net/wlan0 ]; then
    wifi_str="${CY}connecting...${CR} (BCM43455)"
else
    wifi_str="standby (BCM43455)"
fi

printf '\033[2J\033[H\n'
printf '%s\n' "         ${CD}.======.${CR}             ${CY}root${CR}@${CY}nikon-z30${CR}"
printf '%s\n' "   ${CD}.----/${CW} NIKON  ${CD}\\------.${CR}     ${CD}----------------------------------------${CR}"
printf '%s\n' "  ${CD}/ ${CRED}___${CD}/          \\  ${CW}(O)${CD} \\${CR}    ${CY}OS:${CR} Wonder Lantern Linux (${arch})"
printf '%s\n' " ${CD}| ${CRED}|${CD}  |  ${CW}.-\"\"\"\"-.${CD}  |  ${CY}_${CD}  |${CR}    ${CY}Host:${CR} ${host}"
printf '%s\n' " ${CD}| ${CRED}|${CD}  | ${CW}/  ${CC}.--.${CW}  \\${CD} | ${CD}/ \\${CD} |${CR}    ${CY}Kernel:${CR} ${kernel}"
printf '%s\n' " ${CD}| ${CRED}|${CD}  |${CW}|  ${CC}/ /\\ \\${CW}  |${CD}| ${CD}| |${CD} |${CR}    ${CY}Uptime:${CR} ${uptime_str}"
printf '%s\n' " ${CD}| ${CRED}|${CD}  |${CW}|  ${CC}\\ \\/ /${CW}  |${CD}| ${CD}|_|${CD} |${CR}    ${CY}Shell:${CR} BusyBox ${bbver} ash"
printf '%s\n' " ${CD}| ${CRED}|${CD}  | ${CW}\\  ${CC}'--'${CW}  /${CD} |     |${CR}    ${CY}Resolution:${CR} 720x480 (YUV422 overlay)"
printf '%s\n' " ${CD}| ${CRED}|${CD}  |  ${CW}'-....-'${CD}  | ${CY}Z30${CD} |${CR}    ${CY}CPU:${CR} EXPEED 6 Cortex-A7 (${cpus})"
printf '%s\n' "  ${CD}\\___\\____________/____/${CR}     ${CY}GPU:${CR} DMP OpenVG 1.1 (fbcon RGB565)"
printf '%s\n' "                              ${CY}Memory:${CR} ${mem_used}MiB / ${mem_tot}MiB"
printf '%s\n' "     ${CY}///////////////////${CR}      ${CY}SD Card:${CR} ${sd_str}"
printf '%s\n' "    ${CY}////${CW}  NIKON Z30  ${CY}////${CR}     ${CY}Wi-Fi:${CR} ${wifi_str}"
printf '%s\n' "   ${CY}///////////////////${CR}        ${CY}Terminal:${CR} /dev/tty0 (90x30)"
printf '\n'
printf '                              \033[40m   \033[41m   \033[42m   \033[43m   \033[44m   \033[45m   \033[46m   \033[47m   \033[0m\n'
printf '                              \033[40m   \033[41m   \033[42m   \033[43m   \033[44m   \033[45m   \033[46m   \033[47m   \033[0m\n\n'
