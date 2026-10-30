import { expect, test } from "bun:test";
import { decodeLicenseNotices } from "../src/licenseNotices";

const entry = { id: "a", name: "A", version: "1", group: "rust", host: false,
  license: "MIT", source: "https://example.com", attribution: "Owner" };

test("shared license text retains each component and document mapping", () => {
  const result = decodeLicenseNotices({ entries: [
    { ...entry, documents: [{ title: "MIT A", ref: "same" }] },
    { ...entry, id: "b", attribution: "Other", documents: [{ title: "MIT B", ref: "same" }] },
  ], texts: { same: "Complete license text" } });
  expect(result.entries.map(row => [row.id, row.attribution, row.documents[0].title, row.documents[0].text])).toEqual([
    ["a", "Owner", "MIT A", "Complete license text"], ["b", "Other", "MIT B", "Complete license text"],
  ]);
});

test("legacy inline notices remain readable and missing pool references fail", () => {
  expect(decodeLicenseNotices({ entries: [{ ...entry, documents: [{ title: "MIT", text: "Original" }] }] }).entries[0].documents[0].text).toBe("Original");
  expect(() => decodeLicenseNotices({ entries: [{ ...entry, documents: [{ title: "MIT", ref: "missing" }] }], texts: {} })).toThrow("Missing license text");
  expect(() => decodeLicenseNotices({ entries: [{ ...entry, documents: [{ title: "MIT", ref: "toString" }] }], texts: {} })).toThrow("Missing license text");
});
