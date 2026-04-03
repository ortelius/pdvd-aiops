package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/Masterminds/semver/v3"
	"github.com/google/uuid"
	"github.com/package-url/packageurl-go"
	"gopkg.in/yaml.v2"
)

func TestHealthHandler(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	w := httptest.NewRecorder()

	healthHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var resp Response
	if err := json.NewDecoder(w.Body).Decode(&resp); err != nil {
		t.Fatalf("failed to decode response: %v", err)
	}

	if resp.Status != "ok" {
		t.Errorf("expected status 'ok', got '%s'", resp.Status)
	}
}

func TestVersionHandler(t *testing.T) {
	req := httptest.NewRequest("GET", "/version?v=3.2.1-beta.1", nil)
	w := httptest.NewRecorder()

	versionHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var result map[string]interface{}
	json.NewDecoder(w.Body).Decode(&result)

	if result["major"] != float64(3) {
		t.Errorf("expected major 3, got %v", result["major"])
	}
	if result["minor"] != float64(2) {
		t.Errorf("expected minor 2, got %v", result["minor"])
	}
	if result["patch"] != float64(1) {
		t.Errorf("expected patch 1, got %v", result["patch"])
	}
	if result["prerelease"] != "beta.1" {
		t.Errorf("expected prerelease 'beta.1', got %v", result["prerelease"])
	}
}

func TestVersionHandlerInvalid(t *testing.T) {
	req := httptest.NewRequest("GET", "/version?v=not-a-version", nil)
	w := httptest.NewRecorder()

	versionHandler(w, req)

	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400, got %d", w.Code)
	}
}

func TestDependencyHandler(t *testing.T) {
	req := httptest.NewRequest("GET", "/dependency?pkg=my-lib&ver=1.5.0", nil)
	w := httptest.NewRecorder()

	dependencyHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var dep DependencyInfo
	json.NewDecoder(w.Body).Decode(&dep)

	if dep.Name != "my-lib" {
		t.Errorf("expected name 'my-lib', got '%s'", dep.Name)
	}
	if dep.Version != "1.5.0" {
		t.Errorf("expected version '1.5.0', got '%s'", dep.Version)
	}
	if dep.PURL == "" {
		t.Error("expected non-empty PURL")
	}
	if dep.ID == "" {
		t.Error("expected non-empty ID (UUID)")
	}
}

func TestYamlHandler(t *testing.T) {
	req := httptest.NewRequest("GET", "/yaml", nil)
	w := httptest.NewRecorder()

	yamlHandler(w, req)

	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}

	var dep DependencyInfo
	if err := yaml.Unmarshal(w.Body.Bytes(), &dep); err != nil {
		t.Fatalf("failed to parse YAML: %v", err)
	}

	if dep.Name != "test-package" {
		t.Errorf("expected name 'test-package', got '%s'", dep.Name)
	}
	if dep.Version != "2.0.0" {
		t.Errorf("expected version '2.0.0', got '%s'", dep.Version)
	}
}

// Direct dependency unit tests

func TestSemverParsing(t *testing.T) {
	v, err := semver.NewVersion("3.4.0")
	if err != nil {
		t.Fatalf("failed to parse version: %v", err)
	}
	if v.Major() != 3 || v.Minor() != 4 || v.Patch() != 0 {
		t.Errorf("unexpected version: %s", v.String())
	}
}

func TestSemverConstraint(t *testing.T) {
	c, err := semver.NewConstraint(">= 1.0.0, < 2.0.0")
	if err != nil {
		t.Fatalf("failed to parse constraint: %v", err)
	}

	v1, _ := semver.NewVersion("1.5.0")
	v2, _ := semver.NewVersion("2.1.0")

	if !c.Check(v1) {
		t.Error("1.5.0 should satisfy >= 1.0.0, < 2.0.0")
	}
	if c.Check(v2) {
		t.Error("2.1.0 should NOT satisfy >= 1.0.0, < 2.0.0")
	}
}

func TestUUIDGeneration(t *testing.T) {
	id1 := uuid.New()
	id2 := uuid.New()

	if id1 == id2 {
		t.Error("two generated UUIDs should not be equal")
	}
	if id1.String() == "" {
		t.Error("UUID string should not be empty")
	}
}

func TestPackageURL(t *testing.T) {
	purl := packageurl.NewPackageURL("golang", "github.com/google", "uuid", "v1.6.0", nil, "")
	s := purl.ToString()

	if s == "" {
		t.Error("PURL string should not be empty")
	}

	parsed, err := packageurl.FromString(s)
	if err != nil {
		t.Fatalf("failed to parse PURL: %v", err)
	}
	if parsed.Type != "golang" {
		t.Errorf("expected type 'golang', got '%s'", parsed.Type)
	}
	if parsed.Name != "uuid" {
		t.Errorf("expected name 'uuid', got '%s'", parsed.Name)
	}
}

func TestYamlMarshalRoundtrip(t *testing.T) {
	original := DependencyInfo{
		ID:      "test-id",
		Name:    "test-pkg",
		Version: "1.0.0",
		PURL:    "pkg:golang/test-pkg@1.0.0",
	}

	data, err := yaml.Marshal(original)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded DependencyInfo
	if err := yaml.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.Name != original.Name {
		t.Errorf("expected name '%s', got '%s'", original.Name, decoded.Name)
	}
	if decoded.Version != original.Version {
		t.Errorf("expected version '%s', got '%s'", original.Version, decoded.Version)
	}
}
