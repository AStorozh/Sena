#!/bin/zsh
# The whole matrix for the given models: python launch.py per bench, benches two at a time.
# ./run_matrix.sh <models,comma,separated> <tag>
cd "${0:A:h}"
MODELS=$1
TAG=$2
VOICE_CASES=(hello math remember-allergy remind-call remind-oven remind-gym message-mom weather btc usd-vnd moscow-time mac-volume todo idea news shorter)
python3 launch.py brain brain-$TAG $MODELS brain 3.0 &
python3 launch.py web web-$TAG-r1 $MODELS &
wait
python3 launch.py brain voice-$TAG $MODELS voice 1.5 $VOICE_CASES &
python3 launch.py mac mac-$TAG-r1 $MODELS &
wait
python3 launch.py judge judge-$TAG-r1 $MODELS &
python3 launch.py narrow narrow-$TAG-r1 $MODELS &
wait
python3 launch.py web web-$TAG-r2 $MODELS &
python3 launch.py mac mac-$TAG-r2 $MODELS &
wait
python3 launch.py judge judge-$TAG-r2 $MODELS &
python3 launch.py narrow narrow-$TAG-r2 $MODELS &
wait
echo "matrix $TAG done"
