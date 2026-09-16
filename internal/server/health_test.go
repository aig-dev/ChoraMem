package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthHandlerReportsLivenessAndReadiness(t *testing.T) {
	ready := false
	handler := NewHealthHandler(func() bool { return ready })

	t.Run("live", func(t *testing.T) {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/health/live", nil))

		if response.Code != http.StatusOK {
			t.Fatalf("status = %d, want %d", response.Code, http.StatusOK)
		}
		if response.Body.String() != "ok\n" {
			t.Fatalf("body = %q, want %q", response.Body.String(), "ok\n")
		}
	})

	t.Run("not ready", func(t *testing.T) {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/health/ready", nil))

		if response.Code != http.StatusServiceUnavailable {
			t.Fatalf("status = %d, want %d", response.Code, http.StatusServiceUnavailable)
		}
	})

	t.Run("ready", func(t *testing.T) {
		ready = true
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/health/ready", nil))

		if response.Code != http.StatusOK {
			t.Fatalf("status = %d, want %d", response.Code, http.StatusOK)
		}
		if response.Body.String() != "ok\n" {
			t.Fatalf("body = %q, want %q", response.Body.String(), "ok\n")
		}
	})
}

func TestHealthHandlerRejectsOtherMethodsAndPaths(t *testing.T) {
	handler := NewHealthHandler(func() bool { return true })

	tests := []struct {
		name   string
		method string
		path   string
		status int
	}{
		{name: "post live", method: http.MethodPost, path: "/health/live", status: http.StatusMethodNotAllowed},
		{name: "unknown path", method: http.MethodGet, path: "/health", status: http.StatusNotFound},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			response := httptest.NewRecorder()
			handler.ServeHTTP(response, httptest.NewRequest(test.method, test.path, nil))
			if response.Code != test.status {
				t.Fatalf("status = %d, want %d", response.Code, test.status)
			}
		})
	}
}

func TestHandlerKeepsHealthUnauthenticatedBesideDataPlane(t *testing.T) {
	dataCalls := 0
	handler := NewHandler(func() bool { return true }, "/memory.v1.MemoryCore/", http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
		dataCalls++
		response.WriteHeader(http.StatusUnauthorized)
	}))

	health := httptest.NewRecorder()
	handler.ServeHTTP(health, httptest.NewRequest(http.MethodGet, "/health/ready", nil))
	if health.Code != http.StatusOK || dataCalls != 0 {
		t.Fatalf("health status = %d, data calls = %d", health.Code, dataCalls)
	}

	data := httptest.NewRecorder()
	handler.ServeHTTP(data, httptest.NewRequest(http.MethodPost, "/memory.v1.MemoryCore/SelectMemory", nil))
	if data.Code != http.StatusUnauthorized || dataCalls != 1 {
		t.Fatalf("data status = %d, data calls = %d", data.Code, dataCalls)
	}
}
