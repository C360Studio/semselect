package guard

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"unicode/utf8"
)

// Go's struct decoder accepts case variants and duplicate keys, whereas the
// runtime reads case-sensitive JSON. Inspect keys before forwarding the original
// bytes; remarshal would sort the caller's candidate order.
func checkJSON(data []byte) error {
	if !utf8.Valid(data) {
		return errors.New("request must be valid UTF-8")
	}
	d := json.NewDecoder(bytes.NewReader(data))
	var walk func([]string) error
	walk = func(path []string) error {
		if len(path) > 8 {
			return errors.New("JSON nesting exceeds 8 levels")
		}
		token, err := d.Token()
		if err != nil {
			return errors.New("invalid JSON")
		}
		delim, ok := token.(json.Delim)
		if !ok {
			return nil
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for d.More() {
				key, err := d.Token()
				if err != nil {
					return errors.New("invalid JSON key")
				}
				k, ok := key.(string)
				if !ok {
					return errors.New("object keys must be strings")
				}
				if seen[k] {
					return errors.New("duplicate JSON key")
				}
				seen[k] = true
				if len(path) == 0 && k != "state" && k != "model" && k != "questions" {
					return fmt.Errorf("unknown request field %q", k)
				}
				if len(path) == 2 && path[0] == "questions" && k != "type" && k != "instructions" && k != "criteria" {
					return fmt.Errorf("unknown question field %q", k)
				}
				if err := walk(append(path, k)); err != nil {
					return err
				}
			}
		case '[':
			for d.More() {
				if err := walk(append(path, "[]")); err != nil {
					return err
				}
			}
		default:
			return errors.New("unexpected JSON delimiter")
		}
		_, err = d.Token()
		return err
	}
	if err := walk(nil); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("expected exactly one JSON value")
	}
	return nil
}
