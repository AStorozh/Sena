#!/bin/zsh
# Builds the phone app: android/sena.apk.
# Signed with the permanent debug.keystore key, package com.tema.ia: a new version installs over the old one.
# Needs JDK 17 and the Android SDK (platform 37, build-tools 36+); the paths below can be set from outside.
set -e
cd "$(dirname "$0")"
export JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}
export ANDROID_HOME=${ANDROID_HOME:-$HOME/Library/Android/sdk}
[ -f local.properties ] || echo "sdk.dir=$ANDROID_HOME" > local.properties
./gradlew :app:assembleRelease
cp app/build/outputs/apk/release/app-release.apk sena.apk
echo "Готово: $(pwd)/sena.apk ($(du -h sena.apk | cut -f1))"
