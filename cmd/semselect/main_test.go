package main

import "testing"

func TestHealthcheckFollowsListenAddress(t *testing.T) {
	for _, tc := range []struct{ addr, want string }{
		{":8084", "http://127.0.0.1:8084/ready"},
		{"127.0.0.1:9090", "http://127.0.0.1:9090/ready"},
		{"0.0.0.0:8084", "http://127.0.0.1:8084/ready"},
		{"[::]:8084", "http://[::1]:8084/ready"},
		{"[::1]:9090", "http://[::1]:9090/ready"},
	} {
		t.Run(tc.addr, func(t *testing.T) {
			got, err := listenConfig(tc.addr)
			if err != nil || got != tc.want {
				t.Fatalf("got %q, %v; want %q", got, err, tc.want)
			}
		})
	}
	for _, addr := range []string{"127.0.0.1", ":0", ":65536", ":http", "localhost:8084", "http://127.0.0.1:8084"} {
		if _, err := listenConfig(addr); err == nil {
			t.Errorf("accepted invalid listen address %q", addr)
		}
	}
}
