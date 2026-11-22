#!/bin/bash
# 稿 · 发布到 GitHub
#
# 一次性脚本。双击运行，它会：
#   1. 检查 gh（GitHub 命令行工具）装没装、登录没登录
#   2. 把 README/LICENSE 里的 __AUTHOR__ 换成你的 GitHub 用户名
#   3. git init + 第一次提交
#   4. 建一个公开仓库并推上去
#   5. 打一个 zip 当 release 附件
#
# 刻意不开 set -u：macOS 自带 bash 3.2，中文全角字符旁边配裸变量会炸。

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SELF" || exit 1

REPO="transcript"
VERSION="1.0"
ZIPNAME="Transcript-${VERSION}.zip"

B=$'\033[1m'; DIM=$'\033[2m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
hr() { printf "%s──────────────────────────────────────────────%s\n" "$DIM" "$N"; }
ok() { printf "  %s✓%s %s\n" "$G" "$N" "$*"; }
bad() { printf "  %s✗%s %s\n" "$R" "$N" "$*"; }
pause() { echo; printf "%s按回车关闭这个窗口%s" "$DIM" "$N"; read -r _; }

echo
printf "%s稿 · 发布到 GitHub%s\n" "$B" "$N"
hr
echo "要发的文件："
find . -type f \
  -not -path './.git/*' -not -name '*.zip' -not -name '.DS_Store' \
  -not -path '*/__pycache__/*' -not -name 'launch.log' \
  -not -name '给朋友的信.md' \
  | sed 's|^\./|  |' | sort
hr
echo

# ---------------------------------------------------------------- gh
echo "${B}[1/5]${N} 检查 gh"
if ! command -v gh >/dev/null 2>&1; then
  bad "没装 gh（GitHub 的命令行工具）"
  echo
  if command -v brew >/dev/null 2>&1; then
    printf "  用 Homebrew 装？回车继续，Ctrl-C 退出："
    read -r _
    brew install gh || { bad "装失败"; pause; exit 1; }
  else
    echo "  两个办法，任选一个，装完重新双击本文件："
    echo
    echo "  A. 下载安装包，双击装（推荐，不用密码之外的麻烦）："
    echo "     https://github.com/cli/cli/releases/latest"
    echo "     找 macos_arm64.pkg 那个文件"
    echo
    echo "  B. 先装 Homebrew 再 brew install gh：https://brew.sh"
    pause
    exit 1
  fi
fi
ok "$(gh --version | head -1)"

if ! gh auth status >/dev/null 2>&1; then
  bad "gh 还没登录 GitHub"
  echo
  echo "  下面会让你在浏览器里授权一次。选 HTTPS，用浏览器登录。"
  echo
  printf "%s按回车开始登录%s" "$DIM" "$N"
  read -r _
  gh auth login || { bad "登录没完成"; pause; exit 1; }
fi
LOGIN="$(gh api user -q .login 2>/dev/null)"
if [ -z "$LOGIN" ]; then
  bad "登录了但拿不到用户名，试试在终端里跑 gh auth status"
  pause
  exit 1
fi
ok "已登录：$LOGIN"
echo

# ---------------------------------------------------------------- 作者名
echo "${B}[2/5]${N} 填入作者名"
if grep -q '__AUTHOR__' README.md 2>/dev/null; then
  # 不用 sed -i：BSD 和 GNU 的参数不一样，写成临时文件再换最稳
  sed "s/__AUTHOR__/${LOGIN}/" README.md > README.md.tmp && mv README.md.tmp README.md
  if grep -q '__AUTHOR__' README.md; then
    bad "README.md 没换成，手动改一下最后那行版权"
  else
    ok "README.md 里的版权行写成了 ${LOGIN}"
  fi
else
  ok "已经有作者名了，跳过"
fi
echo

# ---------------------------------------------------------------- git
echo "${B}[3/5]${N} 建本地仓库"
if [ -d .git ]; then
  ok "已经是 git 仓库，跳过初始化"
else
  git init -b main >/dev/null 2>&1 || git init >/dev/null 2>&1
  ok "初始化完成（main 分支）"
fi
# 没配过身份的话 commit 会直接失败，这里给仓库单独设一份，不动全局配置
if [ -z "$(git config user.email)" ]; then
  git config --local user.name "$LOGIN"
  git config --local user.email "${LOGIN}@users.noreply.github.com"
  ok "本地身份设为 ${LOGIN}（只对这个仓库生效）"
fi
git add -A
if git diff --cached --quiet 2>/dev/null; then
  ok "没有新改动"
else
  git commit -q -m "稿 ${VERSION}：视频链接 → 本机转写的文字稿" || { bad "提交失败"; pause; exit 1; }
  ok "已提交"
fi
echo

# ---------------------------------------------------------------- 推送
echo "${B}[4/5]${N} 推到 GitHub"
if git remote get-url origin >/dev/null 2>&1; then
  ok "已经有 origin，直接推"
  git push -u origin main || { bad "推送失败"; pause; exit 1; }
else
  echo "  建一个公开仓库 ${LOGIN}/${REPO}"
  printf "%s确认公开？回车继续，Ctrl-C 退出%s" "$DIM" "$N"
  read -r _
  gh repo create "$REPO" --public --source=. --remote=origin --push \
    --description "Transcript（稿）— 给一个视频链接，拿回一份能慢慢读的文字稿。本机转写，音频不上传。" \
    || { bad "建仓库失败（也可能是重名）"; pause; exit 1; }
  gh repo edit --add-topic transcript,whisper,yt-dlp,macos,subtitles,speech-to-text >/dev/null 2>&1 || true
fi
URL="$(gh repo view --json url -q .url 2>/dev/null)"
ok "$URL"
echo

# ---------------------------------------------------------------- release
echo "${B}[5/5]${N} 打 zip 当 release 附件"
STAGE="$(mktemp -d)"
# 发行包里不要开发用的东西
rsync -a --exclude '.git' --exclude '*.zip' --exclude '__pycache__' \
          --exclude '.DS_Store' --exclude 'launch.log' \
          --exclude '发布.command' --exclude '给朋友的信.md' \
          --exclude '.gitignore' \
          ./ "$STAGE/稿/"
(cd "$STAGE" && zip -r -X -q "$SELF/$ZIPNAME" 稿)
rm -rf "$STAGE"
chmod 755 "$SELF/安装.command" "$SELF/稿.command" "$SELF/launch.sh" 2>/dev/null
ok "$ZIPNAME  $(du -h "$SELF/$ZIPNAME" | cut -f1)"

if gh release view "v$VERSION" >/dev/null 2>&1; then
  gh release upload "v$VERSION" "$SELF/$ZIPNAME" --clobber
  ok "已更新到 v$VERSION"
else
  gh release create "v$VERSION" "$SELF/$ZIPNAME" \
    --title "稿 $VERSION" \
    --notes "第一个版本。

- 有字幕的视频几秒钟出稿，没有的本机转写
- whisper.cpp + Metal，不传音频
- 多任务排队、可取消、可删除
- 链接拿不到时，按标题找同版或拖本地文件
- 附带可装进 Claude 的 skill" \
    || { bad "release 没建成，仓库本身已经发好了"; pause; exit 0; }
  ok "v$VERSION 发布完成"
fi

echo
hr
printf "%s发布完成%s\n" "$G" "$N"
echo "  $URL"
echo "  下载页：$URL/releases/latest"
hr
pause
