FROM node:22.15.1

RUN npm install -g pnpm

WORKDIR /app

CMD ["node"]