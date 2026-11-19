FROM node:24-bookworm-slim
WORKDIR /app
COPY server.js index.html ./
CMD ["node", "server.js"]
