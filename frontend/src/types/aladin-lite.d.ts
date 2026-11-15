declare module "aladin-lite" {
  export interface HiPSLayer {
    successCallback?: () => void;
    errorCallback?: () => void;
  }

  export interface AladinViewer {
    getRaDec(): [number, number];
    getFov(): [number, number];
    gotoRaDec(ra: number, dec: number): void;
    setFoV(fov: number): void;
    setFoVRange(min: number, max: number): void;
    setBaseImageLayer(layer: object): void;
    on(event: "positionChanged" | "zoomChanged", callback: (() => void) | null): void;
  }

  const Aladin: {
    init: Promise<void>;
    aladin(host: HTMLDivElement, options: Record<string, unknown>): AladinViewer;
    HiPS(url: string, options: { name: string }): HiPSLayer;
  };
  export default Aladin;
}
