. ~/.profile >/dev/null 2>&1 || true
. ~/.bashrc >/dev/null 2>&1 || true
DREAMINA_BIN=$(command -v dreamina || find ~ -maxdepth 4 -type f -name dreamina 2>/dev/null | head -n 1)
if [ x$DREAMINA_BIN = x ]; then
  echo "dreamina not found. Run install_jimeng_cli.bat first."
  exit 2
fi
$DREAMINA_BIN login
echo
echo "Checking user_credit..."
$DREAMINA_BIN user_credit