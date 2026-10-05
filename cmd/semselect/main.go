package main

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/c360studio/semselect/internal/guard"
)

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func listenConfig(addr string) (string, error) {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return "", fmt.Errorf("SEMSELECT_ADDR must be an IP address and port: %w", err)
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 || (host != "" && net.ParseIP(host) == nil) {
		return "", fmt.Errorf("SEMSELECT_ADDR requires a literal IP (or empty host) and port 1–65535")
	}
	switch host {
	case "", "0.0.0.0":
		host = "127.0.0.1"
	case "::":
		host = "::1"
	}
	return "http://" + net.JoinHostPort(host, port) + "/ready", nil
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)
	// Compose keeps its fixed container port; native launchers bind loopback.
	addr := env("SEMSELECT_ADDR", ":8084")
	healthURL, err := listenConfig(addr)
	if err != nil {
		logger.Error("invalid listen address", "error", err)
		os.Exit(1)
	}
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		client := http.Client{Timeout: 3 * time.Second}
		r, err := client.Get(healthURL)
		if err != nil {
			os.Exit(1)
		}
		r.Body.Close()
		if r.StatusCode != 200 {
			os.Exit(1)
		}
		return
	}
	timeout, err := time.ParseDuration(env("SEMSELECT_TIMEOUT", "120s"))
	if err != nil || timeout <= 0 || timeout > 5*time.Minute {
		logger.Error("SEMSELECT_TIMEOUT must be positive and at most 5m")
		os.Exit(1)
	}
	g, err := guard.New(guard.Config{Upstream: env("SEMSELECT_UPSTREAM", "http://runtime:8080"), Model: env("SEMSELECT_MODEL", "semselect-kev-4b"), Timeout: timeout, Logger: logger})
	if err != nil {
		logger.Error("invalid configuration", "error", err)
		os.Exit(1)
	}
	srv := &http.Server{Addr: addr, Handler: g, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: timeout + 20*time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 8192}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	shutdownDone := make(chan struct{})
	go func() {
		defer close(shutdownDone)
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := srv.Shutdown(shutdown); err != nil {
			logger.Error("shutdown", "error", err)
			_ = srv.Close()
		}
	}()
	logger.Info("semselect_start", "addr", addr, "model", env("SEMSELECT_MODEL", "semselect-kev-4b"))
	if err = srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		logger.Error("serve", "error", err)
		os.Exit(1)
	}
	<-shutdownDone
}
