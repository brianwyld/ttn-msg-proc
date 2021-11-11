#!/bin/sh

sudo docker-compose --env-file ./.env -f docker-compose.yml up --build --remove-orphans -d
