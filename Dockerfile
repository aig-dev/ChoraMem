FROM golang:1.25-alpine AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/memoryd ./cmd/memoryd

FROM alpine:3.23
RUN apk add --no-cache ca-certificates \
    && addgroup -S memory \
    && adduser -S -G memory memory
COPY --from=build /out/memoryd /usr/local/bin/memoryd
USER memory
EXPOSE 8080 8081
ENTRYPOINT ["memoryd"]
