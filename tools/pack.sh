#!/bin/zsh
# Builds the downloadable files: dist/Sena-mac.zip (the code of the last commit, the core and the Mac app's sources,
# built on the user's Mac) and dist/sena.apk (the phone app, if it has been built).
# Only committed files go into the archive. If there is a .publicignore, everything listed in it is left out as well.
set -e
cd "$(dirname "$0")/.."
mkdir -p dist
excludes=()
[[ -f .publicignore ]] && excludes=("${(@f)$(git ls-files -c -i --exclude-from=.publicignore)}")
specs=(.)
for f in $excludes; do [[ -n $f ]] && specs+=(":!$f"); done
rm -f dist/Sena-mac.zip
git archive --format=zip --prefix=Sena/ -o dist/Sena-mac.zip HEAD -- $specs ':!android/sena.apk'
[[ -f android/sena.apk ]] && cp android/sena.apk dist/sena.apk
du -h dist/*
