package server

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/aig-dev/ChoraMem/internal/storage"
)

func TestDiagnosticsRequiresOperatorTokenAndSanitizesFailure(t *testing.T) {
	calls := 0
	read := func(context.Context) (storage.QueueDiagnostics, error) {
		calls++
		return storage.QueueDiagnostics{}, errors.New("private database password")
	}
	for _, token := range []string{"", "incorrect"} {
		request := httptest.NewRequest(http.MethodGet, "/ops/queues", nil)
		request.Header.Set("Authorization", "Bearer "+token)
		response := httptest.NewRecorder()
		NewDiagnosticsHandler("operator-secret", read).ServeHTTP(response, request)
		if response.Code != http.StatusUnauthorized || calls != 0 {
			t.Fatalf("status=%d calls=%d", response.Code, calls)
		}
	}
	request := httptest.NewRequest(http.MethodGet, "/ops/queues", nil)
	request.Header.Set("Authorization", "Bearer operator-secret")
	response := httptest.NewRecorder()
	NewDiagnosticsHandler("operator-secret", read).ServeHTTP(response, request)
	if response.Code != http.StatusServiceUnavailable || strings.Contains(response.Body.String(), "private") || calls != 1 {
		t.Fatalf("status=%d body=%s calls=%d", response.Code, response.Body.String(), calls)
	}
	response = httptest.NewRecorder()
	NewDiagnosticsHandler("", read).ServeHTTP(response, request)
	if response.Code != http.StatusNotFound || calls != 1 {
		t.Fatal("diagnostics enabled without configured token")
	}
}

func TestDiagnosticsReturnsOnlyAggregates(t *testing.T) {
	handler := NewDiagnosticsHandler("token", func(context.Context) (storage.QueueDiagnostics, error) {
		return storage.QueueDiagnostics{Consolidation: storage.QueueStats{Pending: 2, Due: 1, MaxRetries: 3}}, nil
	})
	request := httptest.NewRequest(http.MethodGet, "/ops/queues", nil)
	request.Header.Set("Authorization", "Bearer token")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"max_retries":3`) {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("operator snapshot may be cached")
	}
}
