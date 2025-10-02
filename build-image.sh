#!/bin/bash
# build-image.sh

set -e

IMAGE_NAME="hp-primera-monitor"
IMAGE_TAG="v1.0.0"
TAR_FILE="hp-primera-monitor.tar"

echo "Собираем Docker образ..."
docker build -t ${IMAGE_NAME}:${IMAGE_TAG} .

echo "Сохраняем образ в файл..."
docker save -o ${TAR_FILE} ${IMAGE_NAME}:${IMAGE_TAG}

echo "Сжимаем архив..."
gzip -f ${TAR_FILE}

echo "Готово! Образ сохранен в: ${TAR_FILE}.gz"
echo "Для загрузки использовать:"
echo "docker load -i /path/${TAR_FILE}.gz"