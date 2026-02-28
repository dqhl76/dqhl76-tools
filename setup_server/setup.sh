#!/bin/bash
set -e

echo "开始更新 apt..."
sudo apt-get update -y

echo "正在安装 build-essential and mold..."
sudo apt-get install build-essential -y
sudo apt-get install mold -y

echo "配置 SSH 和 GitHub..."
mkdir -p ~/.ssh
# 1. 密钥必须是 600 权限，不然会报错
chmod 600 ~/.ssh/botgithub 
# 2. 告诉 SSH 遇到 github.com 强行用 botgithub 这个密钥
cat <<EOF >> ~/.ssh/config
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/botgithub
EOF
chmod 600 ~/.ssh/config

echo "正在克隆 databend 仓库..."
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null
git clone git@github.com:dqhl76/databend.git
cd databend
DATABEND_DIR=$(pwd)  # 记录 databend 文件夹的绝对路径

echo "拉取 tags..."
git fetch https://github.com/databendlabs/databend.git --tags

echo "执行 make setup 并自动输入 y..."
yes | make setup

echo "去克隆工具库..."
cd ~
git clone https://github.com/dqhl76/dqhl76-tools.git

echo "正在复制 skills..."
mkdir -p "$DATABEND_DIR/.claude"
cp -r dqhl76-tools/skills "$DATABEND_DIR/.claude/"

echo "开始创建首次编译"

cd ~/databend
source ~/.bashrc
make build

echo "全部搞定！"

