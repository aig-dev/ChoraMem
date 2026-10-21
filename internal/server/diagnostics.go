package server

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"net/http"
	"time"

	"github.com/aig-dev/ChoraMem/internal/storage"
)

// NewDiagnosticsHandler exposes global aggregates only to an explicit operator.
// This separate opt-in credential never grants public Memory lifecycle access.
func NewDiagnosticsHandler(token string, read func(context.Context) (storage.QueueDiagnostics, error)) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		if token == "" || read == nil {
			http.NotFound(w, r)
			return
		}
		if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+token)) != 1 {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		snapshot, err := read(ctx)
		if err != nil {
			http.Error(w, "queue diagnostics unavailable", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(snapshot)
	})
}
