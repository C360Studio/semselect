package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
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

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)
	// Compose configures the host port; the container port is fixed.
	const addr = ":8084"
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		client := http.Client{Timeout: 3 * time.Second}
		r, err := client.Get("http://127.0.0.1:8084/ready")
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
