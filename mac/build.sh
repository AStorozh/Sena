#!/bin/zsh
# Builds the native Mac app (SwiftUI) without an Xcode project: swiftc from the Command Line Tools. Result: mac/Sena.app
# The app draws its Dock and menu bar icons itself from the leaf's outlines (Sena --icon during the build).
set -e
cd "$(dirname "$0")"
APP=Sena.app
rm -rf build "$APP"
mkdir -p build "$APP/Contents/MacOS" "$APP/Contents/Resources"
xcrun swiftc -O -swift-version 5 -parse-as-library -target arm64-apple-macos14.0 \
  -framework Cocoa -framework SwiftUI -framework Charts -framework UserNotifications -framework UniformTypeIdentifiers -framework AVFoundation -framework SceneKit -framework LocalAuthentication \
  -o "$APP/Contents/MacOS/Sena" Sources/*.swift Sources/Views/*.swift
"$APP/Contents/MacOS/Sena" --icon build/icon.iconset
iconutil -c icns build/icon.iconset -o "$APP/Contents/Resources/Sena.icns"
if ACTOOL=$(xcrun --find actool 2>/dev/null); then   # an asset catalog: newer macOS versions take the Dock icon from it
  rm -rf build/Assets.xcassets && mkdir -p build/Assets.xcassets/AppIcon.appiconset
  cp build/icon.iconset/*.png build/Assets.xcassets/AppIcon.appiconset/
  cp AppIcon.json build/Assets.xcassets/AppIcon.appiconset/Contents.json
  echo '{"info":{"version":1,"author":"sena"}}' > build/Assets.xcassets/Contents.json
  "$ACTOOL" build/Assets.xcassets --compile "$APP/Contents/Resources" --platform macosx --minimum-deployment-target 14.0 \
    --app-icon AppIcon --output-partial-info-plist build/icon.plist >/dev/null 2>build/actool.log || echo "actool не справился, см. build/actool.log"
fi
cp Info.plist "$APP/Contents/Info.plist"
mkdir -p "$APP/Contents/Resources/ru.lproj" "$APP/Contents/Resources/en.lproj"   # translations: Localizable.strings, the system picks the language
cp Resources/ru.lproj/*.strings "$APP/Contents/Resources/ru.lproj/"   # Localizable.strings — the app's texts, InfoPlist.strings — its name and the system's questions (microphone, folder)
cp Resources/en.lproj/*.strings "$APP/Contents/Resources/en.lproj/"
# Signed with the permanent developer certificate (from Xcode): the app keeps one identity from build to build, so macOS asks
# for the Desktop folder and the microphone once, not after every rebuild. No certificate, or an expired one — an ad-hoc
# signature (then the question comes back after every build).
IDENTITY=$(security find-identity -v -p codesigning 2>/dev/null | grep -m1 "Apple Development" | sed 's/.*"\(.*\)".*/\1/')
if [ -n "$IDENTITY" ] && codesign --force --sign "$IDENTITY" --timestamp=none "$APP" >/dev/null 2>&1; then
  echo "Подпись: $IDENTITY"
else
  codesign --force --sign - "$APP" >/dev/null 2>&1 || true
  echo "Подпись локальная: сертификата разработчика нет, доступ к папке будут спрашивать после каждой сборки"
fi
echo "Готово: $(pwd)/$APP"
echo "Запуск: open $APP    В Программы: cp -R $APP /Applications/"
