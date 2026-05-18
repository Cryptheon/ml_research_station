/// <reference types="vite/client" />

declare module "katex/contrib/auto-render" {
  function renderMathInElement(el: Element, opts?: object): void;
  export default renderMathInElement;
}
