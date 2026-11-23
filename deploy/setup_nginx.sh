#!/bin/bash
set -e
rm -f /etc/nginx/sites-enabled/default
cp /opt/classroom/deploy/nginx-classroom.conf /etc/nginx/conf.d/classroom.conf
nginx -t
systemctl restart nginx
echo NGINX_OK
