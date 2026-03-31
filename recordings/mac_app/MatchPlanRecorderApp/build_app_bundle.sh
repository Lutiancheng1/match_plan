#!/bin/zsh
set -euo pipefail

APP_NAME="MatchPlanRecorderApp"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/.build"
DIST_DIR="$SCRIPT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
RELEASE_BIN="$BUILD_DIR/arm64-apple-macosx/release/$APP_NAME"
PLIST_PATH="$CONTENTS_DIR/Info.plist"
ICON_GENERATOR="$SCRIPT_DIR/Resources/generate_app_icon.py"
ICONSET_DIR="$SCRIPT_DIR/Resources/AppIcon.iconset"
ICNS_PATH="$SCRIPT_DIR/Resources/AppIcon.icns"

mkdir -p "$DIST_DIR"

echo "[1/5] Building release binary..."
cd "$SCRIPT_DIR"
swift build -c release

if [[ ! -x "$RELEASE_BIN" ]]; then
  echo "release binary not found: $RELEASE_BIN" >&2
  exit 1
fi

echo "[2/5] Generating app icon..."
python3 "$ICON_GENERATOR"
rm -f "$ICNS_PATH"
iconutil -c icns "$ICONSET_DIR" -o "$ICNS_PATH"

echo "[3/5] Creating app bundle..."
rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
cp "$RELEASE_BIN" "$MACOS_DIR/$APP_NAME"
chmod +x "$MACOS_DIR/$APP_NAME"
cp "$ICNS_PATH" "$RESOURCES_DIR/AppIcon.icns"

cat > "$PLIST_PATH" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>MatchPlanRecorderApp</string>
  <key>CFBundleIdentifier</key>
  <string>com.matchplan.recorder</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleName</key>
  <string>MatchPlan Recorder</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

echo -n "APPL????" > "$CONTENTS_DIR/PkgInfo"

if command -v codesign >/dev/null 2>&1; then
  echo "[4/5] Ad-hoc codesign..."
  codesign --force --deep --sign - "$APP_DIR" >/dev/null 2>&1 || true
fi

echo "[5/5] Done"
echo "$APP_DIR"
