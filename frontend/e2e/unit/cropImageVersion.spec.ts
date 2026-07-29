import { expect, test } from "@playwright/test";

import { cropImageVersion } from "../../lib/api";

/**
 * A crop's image lives behind /crops/{id}/image, a URL that says WHICH crop but
 * not WHICH VERSION of it -- and editing a crop rewrites the file in place. An
 * <img> already in the DOM therefore never re-requests anything and keeps showing
 * the pre-edit cell. Rotation is the worst case: it does not even move the box
 * origin, so nothing about the request changes.
 *
 * Cache headers cannot repair this -- must-revalidate governs what happens when a
 * request is made, it does not cause one. These tests pin the two properties the
 * version token must have: it MUST change on every re-cut, and it MUST NOT change
 * otherwise (an unstable token would refetch every crop on every render).
 */

const base = { bbox_x: 10, bbox_y: 20, bbox_w: 100, bbox_h: 80, bbox_angle: 0 };

test("rotation alone changes the version", () => {
  // The exact case the user hit: same origin, same size, only the angle moved.
  expect(cropImageVersion({ ...base, bbox_angle: 30 })).not.toBe(
    cropImageVersion(base)
  );
});

test("moving or resizing the box changes the version", () => {
  for (const change of [
    { bbox_x: 11 },
    { bbox_y: 21 },
    { bbox_w: 101 },
    { bbox_h: 81 },
  ]) {
    expect(cropImageVersion({ ...base, ...change })).not.toBe(
      cropImageVersion(base)
    );
  }
});

test("identical geometry yields an identical version", () => {
  // Stability is load-bearing: a token that varied per render (a timestamp, say)
  // would defeat the 304 and refetch every crop in the gallery every time.
  expect(cropImageVersion({ ...base })).toBe(cropImageVersion(base));
});

test("an unset angle reads as axis-aligned, like the NULL column", () => {
  // bbox_angle is nullable; NULL and 0 are the same crop and must not differ,
  // or every pre-rotation crop would be refetched once for nothing.
  const zero = cropImageVersion(base);
  expect(cropImageVersion({ ...base, bbox_angle: null })).toBe(zero);
  expect(cropImageVersion({ ...base, bbox_angle: undefined })).toBe(zero);
});

test("float noise below the rounding step does not churn the URL", () => {
  expect(cropImageVersion({ ...base, bbox_angle: 30.0001 })).toBe(
    cropImageVersion({ ...base, bbox_angle: 30 })
  );
  // ...but a rotation a user could actually perform still registers.
  expect(cropImageVersion({ ...base, bbox_angle: 30.02 })).not.toBe(
    cropImageVersion({ ...base, bbox_angle: 30 })
  );
});

test("negative and positive rotations are distinct", () => {
  expect(cropImageVersion({ ...base, bbox_angle: -45 })).not.toBe(
    cropImageVersion({ ...base, bbox_angle: 45 })
  );
});
