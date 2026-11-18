#!/bin/sh
# Põe o Pokemon Companion no menu de aplicativos com o ícone (Linux).
# Rode de dentro da pasta extraída: ./install.sh  (desfazer: ./install.sh --remove)
# O nome do .desktop é o mesmo de setDesktopFileName (ui/app_icon.py): é por
# ele que o Wayland associa a janela ao ícone.
set -e
APP_ID=pokemon-companion
HERE=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
DESKTOP="$DATA/applications/$APP_ID.desktop"

if [ "$1" = "--remove" ]; then
    rm -f "$DESKTOP"
    for size in 16 24 32 48 64 128 256 512; do
        rm -f "$DATA/icons/hicolor/${size}x${size}/apps/$APP_ID.png"
    done
    echo "Removido do menu."
    exit 0
fi

for size in 16 24 32 48 64 128 256 512; do
    mkdir -p "$DATA/icons/hicolor/${size}x${size}/apps"
    cp "$HERE/_internal/pokemon_companion/ui/icons/app_$size.png" \
        "$DATA/icons/hicolor/${size}x${size}/apps/$APP_ID.png"
done
mkdir -p "$DATA/applications"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Pokemon Companion
Comment=Treino de Pokémon TCG contra IA
Exec="$HERE/Pokemon Companion"
Icon=$APP_ID
Terminal=false
Categories=Game;CardGame;
StartupWMClass=$APP_ID
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
echo "Pronto: Pokemon Companion está no menu de aplicativos."
