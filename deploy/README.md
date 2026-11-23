# 部署说明（Docker / 华为云 ECS）

对应《工程实现方案》§8 第 9 步：Docker 打包，部署到 ECS，公网可访问。

## 1. 先构建前端静态文件

```bash
cd frontend
npm install
npm run build          # 产物在 frontend/dist
```

## 2. 准备 .env

```bash
cp .env.example .env    # 填 MAAS_API_KEY / HW_AK / HW_SK / HW_PROJECT_ID
```

> 没有密钥也能部署演示：.env 留空 + MOCK_MODE=1，接口会返回 Mock 数据。

## 3. 起容器

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

- 前端：http://服务器IP:8080
- 后端文档：http://服务器IP:8000/docs

## 4. 公网 HTTPS / WSS（必须）

浏览器只在**安全上下文**（HTTPS 或 localhost）允许摄像头与麦克风，
所以公网演示必须给域名配证书（Nginx 加 443 + certbot，或直接用华为云 ELB/WAF 的证书）。

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;
    client_max_body_size 10m;
    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    root /usr/share/nginx/html;
    location / { try_files $uri $uri/ /index.html; }
    location /api/ {
        proxy_pass http://api:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 60s;
    }
}
```

## 5. ECS 安全组

| 端口 | 用途 |
|---|---|
| 8080（或 443） | 前端页面 |
| 8000 | 后端 API（建议只在内网暴露，由 Nginx 代理） |
