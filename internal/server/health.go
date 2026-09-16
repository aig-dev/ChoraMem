package server

import (
	"net/http"
)

func NewHealthHandler(ready func() bool) http.Handler {
	return NewHandler(ready, "", nil)
}

func NewHandler(ready func() bool, dataPlanePath string, dataPlaneHandler http.Handler) http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", func(response http.ResponseWriter, _ *http.Request) {
		writeHealth(response, http.StatusOK)
	})
	mux.HandleFunc("GET /health/ready", func(response http.ResponseWriter, _ *http.Request) {
		if ready == nil || !ready() {
			writeHealth(response, http.StatusServiceUnavailable)
			return
		}
		writeHealth(response, http.StatusOK)
	})
	if dataPlanePath != "" && dataPlaneHandler != nil {
		mux.Handle(dataPlanePath, dataPlaneHandler)
	}
	return mux
}

func writeHealth(response http.ResponseWriter, status int) {
	response.Header().Set("Content-Type", "text/plain; charset=utf-8")
	response.WriteHeader(status)
	_, _ = response.Write([]byte("ok\n"))
}
