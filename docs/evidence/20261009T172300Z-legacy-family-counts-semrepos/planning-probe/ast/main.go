// Throwaway planning probe: per-file entity counts from semsource's own AST
// parsers (read-only import). Not evidence; the stack ingest is authoritative.
package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	semsourceast "github.com/c360studio/semsource/source/ast"
	_ "github.com/c360studio/semsource/source/ast/golang"
	_ "github.com/c360studio/semsource/source/ast/python"
	_ "github.com/c360studio/semsource/source/ast/svelte"
	_ "github.com/c360studio/semsource/source/ast/ts"
)

func main() {
	root := os.Args[1]
	fams := os.Args[2:]
	ext2lang := map[string]string{".go": "go", ".py": "python", ".svelte": "svelte"}
	for _, l := range []string{"typescript", "javascript"} {
		for _, e := range semsourceast.DefaultRegistry.GetExtensionsForParser(l) {
			ext2lang[e] = l
		}
	}
	for _, fam := range fams {
		ws := filepath.Join(root, fam)
		ids := map[string]bool{}
		_ = filepath.Walk(ws, func(p string, info os.FileInfo, err error) error {
			if err != nil || info.IsDir() {
				return err
			}
			lang, ok := ext2lang[strings.ToLower(filepath.Ext(p))]
			if !ok {
				return nil
			}
			parser, err := semsourceast.DefaultRegistry.CreateParser(lang, "semselect", fam, ws)
			if err != nil {
				fmt.Println("ERR", err)
				return nil
			}
			res, err := parser.ParseFile(context.Background(), p)
			if err != nil {
				fmt.Println("ERR", p, err)
				return nil
			}
			n := 0
			kinds := map[string]int{}
			for _, e := range res.Entities {
				if !ids[e.ID] {
					ids[e.ID] = true
					n++
					kinds[string(e.Type)]++
				}
			}
			rel, _ := filepath.Rel(ws, p)
			fmt.Printf("%s\t%s\t%d\t%v\n", fam, rel, n, kinds)
			return nil
		})
		fmt.Printf("%s\tTOTAL_UNIQUE\t%d\n", fam, len(ids))
	}
}
