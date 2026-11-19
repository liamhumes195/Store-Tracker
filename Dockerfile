FROM node:24-bookworm-slim
WORKDIR /app
COPY server.js ./
CMD ["node", "server.js"]
