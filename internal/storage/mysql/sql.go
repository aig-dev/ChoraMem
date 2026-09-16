package mysql

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"
	"time"
)

var errNoRows = sql.ErrNoRows

type isolationLevel uint8
type accessMode uint8

const (
	repeatableRead isolationLevel = iota + 1
	readOnly       accessMode     = iota + 1
)

type txOptions struct {
	IsoLevel   isolationLevel
	AccessMode accessMode
}

type database struct {
	db *sql.DB
}

func (database *database) BeginTx(ctx context.Context, options txOptions) (*transaction, error) {
	connection, err := database.db.Conn(ctx)
	if err != nil {
		return nil, err
	}
	isolation := sql.LevelDefault
	if options.IsoLevel == repeatableRead {
		isolation = sql.LevelRepeatableRead
	}
	tx, err := connection.BeginTx(ctx, &sql.TxOptions{
		Isolation: isolation,
		ReadOnly:  options.AccessMode == readOnly,
	})
	if err != nil {
		connection.Close()
		return nil, err
	}
	return &transaction{tx: tx, connection: connection, locks: make(map[string]struct{})}, nil
}

func (database *database) Exec(ctx context.Context, query string, args ...any) (commandTag, error) {
	boundQuery, boundArgs, err := bindQuery(query, args)
	if err != nil {
		return commandTag{}, err
	}
	result, err := database.db.ExecContext(ctx, boundQuery, boundArgs...)
	return resultTag(result, err)
}

func (database *database) Query(ctx context.Context, query string, args ...any) (rows, error) {
	boundQuery, boundArgs, err := bindQuery(query, args)
	if err != nil {
		return nil, err
	}
	result, err := database.db.QueryContext(ctx, boundQuery, boundArgs...)
	if err != nil {
		return nil, err
	}
	return &sqlRows{rows: result}, nil
}

func (database *database) QueryRow(ctx context.Context, query string, args ...any) row {
	boundQuery, boundArgs, err := bindQuery(query, args)
	if err != nil {
		return &sqlRow{err: err}
	}
	return &sqlRow{row: database.db.QueryRowContext(ctx, boundQuery, boundArgs...)}
}

func (database *database) Ping(ctx context.Context) error { return database.db.PingContext(ctx) }
func (database *database) Close()                         { _ = database.db.Close() }

type transaction struct {
	tx         *sql.Tx
	connection *sql.Conn
	locks      map[string]struct{}
	mu         sync.Mutex
	done       bool
}

func (tx *transaction) Exec(ctx context.Context, query string, args ...any) (commandTag, error) {
	boundQuery, boundArgs, err := bindQuery(query, args)
	if err != nil {
		return commandTag{}, err
	}
	result, err := tx.tx.ExecContext(ctx, boundQuery, boundArgs...)
	return resultTag(result, err)
}

func (tx *transaction) Query(ctx context.Context, query string, args ...any) (rows, error) {
	boundQuery, boundArgs, err := bindQuery(query, args)
	if err != nil {
		return nil, err
	}
	result, err := tx.tx.QueryContext(ctx, boundQuery, boundArgs...)
	if err != nil {
		return nil, err
	}
	return &sqlRows{rows: result}, nil
}

func (tx *transaction) QueryRow(ctx context.Context, query string, args ...any) row {
	boundQuery, boundArgs, err := bindQuery(query, args)
	if err != nil {
		return &sqlRow{err: err}
	}
	return &sqlRow{row: tx.tx.QueryRowContext(ctx, boundQuery, boundArgs...)}
}

// acquireLock provides the transaction-scoped serialization used by the
// PostgreSQL adapter through advisory locks. MySQL named locks are
// connection-scoped, so transaction owns and releases each name exactly once.
func (tx *transaction) acquireLock(ctx context.Context, key int64) error {
	name := fmt.Sprintf("memory-core:%016x", uint64(key))
	tx.mu.Lock()
	if _, exists := tx.locks[name]; exists {
		tx.mu.Unlock()
		return nil
	}
	tx.mu.Unlock()

	timeoutSeconds := int64(30)
	if deadline, ok := ctx.Deadline(); ok {
		remaining := time.Until(deadline)
		if remaining <= 0 {
			return context.DeadlineExceeded
		}
		timeoutSeconds = int64(remaining.Round(time.Second) / time.Second)
		if timeoutSeconds < 1 {
			timeoutSeconds = 1
		}
		if timeoutSeconds > 30 {
			timeoutSeconds = 30
		}
	}
	var acquired sql.NullInt64
	if err := tx.tx.QueryRowContext(ctx, "SELECT GET_LOCK(?, ?)", name, timeoutSeconds).Scan(&acquired); err != nil {
		return err
	}
	if !acquired.Valid || acquired.Int64 != 1 {
		return fmt.Errorf("acquire MySQL named lock %s: timed out", name)
	}
	tx.mu.Lock()
	tx.locks[name] = struct{}{}
	tx.mu.Unlock()
	return nil
}

func (tx *transaction) Commit(ctx context.Context) error {
	tx.mu.Lock()
	if tx.done {
		tx.mu.Unlock()
		return sql.ErrTxDone
	}
	tx.done = true
	tx.mu.Unlock()
	commitErr := tx.tx.Commit()
	releaseErr := tx.releaseLocks(ctx)
	closeErr := tx.connection.Close()
	return errors.Join(commitErr, releaseErr, closeErr)
}

func (tx *transaction) Rollback(ctx context.Context) error {
	tx.mu.Lock()
	if tx.done {
		tx.mu.Unlock()
		return sql.ErrTxDone
	}
	tx.done = true
	tx.mu.Unlock()
	rollbackErr := tx.tx.Rollback()
	releaseErr := tx.releaseLocks(ctx)
	closeErr := tx.connection.Close()
	return errors.Join(rollbackErr, releaseErr, closeErr)
}

func (tx *transaction) releaseLocks(ctx context.Context) error {
	tx.mu.Lock()
	locks := make([]string, 0, len(tx.locks))
	for name := range tx.locks {
		locks = append(locks, name)
	}
	tx.locks = nil
	tx.mu.Unlock()
	var result error
	for _, name := range locks {
		var released sql.NullInt64
		if err := tx.connection.QueryRowContext(ctx, "SELECT RELEASE_LOCK(?)", name).Scan(&released); err != nil {
			result = errors.Join(result, err)
			continue
		}
		if !released.Valid || released.Int64 != 1 {
			result = errors.Join(result, fmt.Errorf("release MySQL named lock %s", name))
		}
	}
	return result
}

type commandTag struct{ rowsAffected int64 }

func (tag commandTag) RowsAffected() int64 { return tag.rowsAffected }

func resultTag(result sql.Result, err error) (commandTag, error) {
	if err != nil {
		return commandTag{}, err
	}
	rowsAffected, err := result.RowsAffected()
	if err != nil {
		return commandTag{}, err
	}
	return commandTag{rowsAffected: rowsAffected}, nil
}

type row interface {
	Scan(...any) error
}

type rows interface {
	Next() bool
	Scan(...any) error
	Err() error
	Close()
}

type sqlRow struct {
	row *sql.Row
	err error
}

func (row *sqlRow) Scan(destinations ...any) error {
	if row.err != nil {
		return row.err
	}
	return row.row.Scan(adaptScanDestinations(destinations)...)
}

type sqlRows struct{ rows *sql.Rows }

func (rows *sqlRows) Next() bool { return rows.rows.Next() }
func (rows *sqlRows) Err() error { return rows.rows.Err() }
func (rows *sqlRows) Close()     { _ = rows.rows.Close() }
func (rows *sqlRows) Scan(destinations ...any) error {
	return rows.rows.Scan(adaptScanDestinations(destinations)...)
}

type jsonStringSliceScanner struct{ destination *[]string }

func (scanner jsonStringSliceScanner) Scan(source any) error {
	if source == nil {
		*scanner.destination = nil
		return nil
	}
	var encoded []byte
	switch value := source.(type) {
	case []byte:
		encoded = value
	case string:
		encoded = []byte(value)
	default:
		return fmt.Errorf("scan JSON string list from %T", source)
	}
	return json.Unmarshal(encoded, scanner.destination)
}

func adaptScanDestinations(destinations []any) []any {
	adapted := make([]any, len(destinations))
	for index, destination := range destinations {
		if list, ok := destination.(*[]string); ok {
			adapted[index] = jsonStringSliceScanner{destination: list}
			continue
		}
		adapted[index] = destination
	}
	return adapted
}

func bindQuery(query string, args []any) (string, []any, error) {
	var output strings.Builder
	output.Grow(len(query))
	boundArgs := make([]any, 0, len(args))
	for index := 0; index < len(query); {
		if query[index] != '$' || index+1 >= len(query) || query[index+1] < '0' || query[index+1] > '9' {
			output.WriteByte(query[index])
			index++
			continue
		}
		end := index + 1
		for end < len(query) && query[end] >= '0' && query[end] <= '9' {
			end++
		}
		ordinal, err := strconv.Atoi(query[index+1 : end])
		if err != nil || ordinal < 1 || ordinal > len(args) {
			return "", nil, fmt.Errorf("invalid SQL placeholder %q", query[index:end])
		}
		output.WriteByte('?')
		boundArgs = append(boundArgs, databaseArgument(args[ordinal-1]))
		index = end
	}
	return output.String(), boundArgs, nil
}

func databaseArgument(value any) any {
	switch refs := value.(type) {
	case []string:
		if refs == nil {
			return nil
		}
		encoded, _ := json.Marshal(refs)
		return string(encoded)
	case driver.Valuer:
		return value
	default:
		return value
	}
}
