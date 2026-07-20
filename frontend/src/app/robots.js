// The whole app sits behind auth with no separate public marketing surface
// to index (see layout.jsx's `metadata.robots` for the matching meta-tag
// directive, which covers crawlers that render the page anyway).
export default function robots() {
  return {
    rules: {
      userAgent: "*",
      disallow: "/",
    },
  };
}
