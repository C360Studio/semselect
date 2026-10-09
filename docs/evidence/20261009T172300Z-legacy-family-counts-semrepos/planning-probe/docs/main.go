// Throwaway planning probe: per-file doc/chunk counts from semsource's doc
// handler (read-only import). Not evidence; the stack ingest is authoritative.
package main

import (
	"context"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/c360studio/semsource/handler/doc"
	"github.com/c360studio/semsource/storage/filestore"
)

type cfg struct{ paths []string }

func (c cfg) GetType() string               { return "docs" }
func (c cfg) GetPath() string               { return "" }
func (c cfg) GetPaths() []string            { return c.paths }
func (c cfg) GetURL() string                { return "" }
func (c cfg) GetBranch() string             { return "" }
func (c cfg) IsWatchEnabled() bool          { return false }
func (c cfg) GetKeyframeMode() string       { return "" }
func (c cfg) GetKeyframeInterval() string   { return "" }
func (c cfg) GetSceneThreshold() float64    { return 0 }
func (c cfg) GetCoalesceMs() int            { return 0 }

func main() {
	root, tmp := os.Args[1], os.Args[2]
	for _, fam := range os.Args[3:] {
		ws := filepath.Join(root, fam)
		total := 0
		_ = filepath.Walk(ws, func(p string, info os.FileInfo, err error) error {
			if err != nil || info.IsDir() {
				return err
			}
			switch strings.ToLower(filepath.Ext(p)) {
			case ".md", ".mdx", ".adoc", ".txt":
			default:
				return nil
			}
			dir, _ := os.MkdirTemp(tmp, "one-")
			src, _ := os.Open(p)
			dst, _ := os.Create(filepath.Join(dir, filepath.Base(p)))
			_, _ = io.Copy(dst, src)
			src.Close()
			dst.Close()
			store, serr := filestore.New(filepath.Join(tmp, "bodies"), true)
			if serr != nil {
				fmt.Println("ERR", serr)
				return nil
			}
			states, err := doc.New(doc.WithBodyStore(store, "probe")).IngestEntityStates(context.Background(), cfg{paths: []string{dir}}, "semselect")
			if err != nil {
				fmt.Println("ERR", p, err)
				return nil
			}
			rel, _ := filepath.Rel(ws, p)
			fmt.Printf("%s\t%s\t%d\t%d\n", fam, rel, len(states), info.Size())
			total += len(states)
			return nil
		})
		fmt.Printf("%s\tTOTAL\t%d\n", fam, total)
	}
}
