#!/bin/sh

sudo docker-compose -f docker-compose.yml up --env-file ./.env --build --remove-orphans -d


