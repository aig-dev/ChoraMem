// Package fault defines retry semantics shared by Core transports and stores.
package fault

import "errors"

var (
	// ErrUnavailable means the operation may be retried with the same
	// idempotency key because the storage result was unavailable or uncertain.
	ErrUnavailable = errors.New("memory core unavailable")

	// ErrAborted means a concurrent transaction prevented this attempt from
	// committing; retrying the complete operation is safe.
	ErrAborted = errors.New("memory core operation aborted")
)
