FROM node:24.15.0-slim
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY shared/ /app/shared/
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
