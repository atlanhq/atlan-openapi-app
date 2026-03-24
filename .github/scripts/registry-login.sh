#!/bin/bash

if [ "$#" -ne 3 ]; then
    echo "Usage: $0 <registry_url> <username> <password>"
    exit 1
fi

REGISTRY_URL="$1"
USERNAME="$2"
PASSWORD="$3"

MAX_RETRIES=10
RETRY_INTERVAL=5

docker_login() {
    echo "$PASSWORD" | docker login "$REGISTRY_URL" -u "$USERNAME" --password-stdin
}

for ((i=1; i<=MAX_RETRIES; i++)); do
    echo "Attempt $i: Logging in to Docker registry..."
    if docker_login; then
        echo "Login successful."
        break
    else
        echo "Login failed."
        if [ "$i" -lt "$MAX_RETRIES" ]; then
            echo "Retrying in $RETRY_INTERVAL seconds..."
            sleep $RETRY_INTERVAL
        else
            echo "Exceeded maximum number of retries. Exiting."
            exit 1
        fi
    fi
done
