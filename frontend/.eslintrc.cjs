module.exports = {
  root: true,
  env: { browser: true, es2020: true },
  extends: [
    "eslint:recommended",
    "plugin:@typescript-eslint/recommended",
    "plugin:react-hooks/recommended",
  ],
  ignorePatterns: ["dist", ".eslintrc.cjs"],
  parser: "@typescript-eslint/parser",
  plugins: ["react-refresh"],
  rules: {
    "react-refresh/only-export-components": [
      "warn",
      { allowConstantExport: true },
    ],
    // D3 force-simulation nodes mutate with x/y — allow explicit any there.
    "@typescript-eslint/no-explicit-any": "warn",
    // Allow non-null assertions in React refs that are always assigned before use.
    "@typescript-eslint/no-non-null-assertion": "warn",
    // Unused vars: error for variables, warn for args (common in callbacks).
    "@typescript-eslint/no-unused-vars": [
      "error",
      { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
    ],
  },
};
