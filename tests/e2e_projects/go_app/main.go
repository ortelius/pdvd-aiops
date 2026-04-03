package main

import (
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/Masterminds/semver/v3"
	"github.com/google/uuid"
	"github.com/package-url/packageurl-go"
	"go.uber.org/zap"
	"gopkg.in/yaml.v2"
)

type Response struct {
	Status  string `json:"status"`
	Message string `json:"message"`
}

type DependencyInfo struct {
	ID      string `json:"id" yaml:"id"`
	Name    string `json:"name" yaml:"name"`
	Version string `json:"version" yaml:"version"`
	PURL    string `json:"purl" yaml:"purl"`
}

var logger *zap.Logger

func init() {
	logger, _ = zap.NewDevelopment()
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	logger.Info("health check")
	resp := Response{Status: "ok", Message: "E2E go test app"}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func versionHandler(w http.ResponseWriter, r *http.Request) {
	ver := r.URL.Query().Get("v")
	if ver == "" {
		ver = "1.0.0"
	}

	sv, err := semver.NewVersion(ver)
	if err != nil {
		http.Error(w, fmt.Sprintf("invalid semver: %s", err), http.StatusBadRequest)
		return
	}

	logger.Info("parsed version", zap.String("version", sv.String()))

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"major":      sv.Major(),
		"minor":      sv.Minor(),
		"patch":      sv.Patch(),
		"prerelease": sv.Prerelease(),
		"original":   sv.Original(),
	})
}

func dependencyHandler(w http.ResponseWriter, r *http.Request) {
	pkg := r.URL.Query().Get("pkg")
	ver := r.URL.Query().Get("ver")
	if pkg == "" {
		pkg = "example-pkg"
	}
	if ver == "" {
		ver = "1.0.0"
	}

	purl := packageurl.NewPackageURL("golang", "", pkg, ver, nil, "")

	dep := DependencyInfo{
		ID:      uuid.New().String(),
		Name:    pkg,
		Version: ver,
		PURL:    purl.ToString(),
	}

	logger.Info("created dependency info",
		zap.String("name", dep.Name),
		zap.String("purl", dep.PURL),
	)

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(dep)
}

func yamlHandler(w http.ResponseWriter, r *http.Request) {
	dep := DependencyInfo{
		ID:      uuid.New().String(),
		Name:    "test-package",
		Version: "2.0.0",
		PURL:    "pkg:golang/test-package@2.0.0",
	}

	data, err := yaml.Marshal(dep)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/yaml")
	w.Write(data)
}

func main() {
	http.HandleFunc("/", healthHandler)
	http.HandleFunc("/version", versionHandler)
	http.HandleFunc("/dependency", dependencyHandler)
	http.HandleFunc("/yaml", yamlHandler)
	fmt.Println("Listening on :8080")
	http.ListenAndServe(":8080", nil)
}
