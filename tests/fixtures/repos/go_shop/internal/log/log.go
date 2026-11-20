package log

import "fmt"

// Logger prints lines with a prefix.
type Logger struct{ prefix string }

// New makes a Logger.
func New(prefix string) *Logger { return &Logger{prefix: prefix} }

// Info prints one line.
func (l *Logger) Info(msg string) { fmt.Println(l.prefix + ": " + msg) }
