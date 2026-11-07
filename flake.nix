{
  description = "humble-integrator: get Humble Bundle purchases into Steam on macOS and Linux";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs:
        let
          inherit (pkgs) lib;
          # 7-Zip unpacks vendor self-extractors; umu-launcher runs Windows builds with Proton on Linux.
          runtimePath = [ pkgs._7zz ] ++ lib.optionals pkgs.stdenv.isLinux
            (lib.filter (p: p != null) [ (pkgs.umu-launcher or null) ]);
        in
        {
          default = pkgs.stdenvNoCC.mkDerivation {
            pname = "humble-integrator";
            version = "0.1.0";
            src = lib.fileset.toSource {
              root = ./.;
              fileset = lib.fileset.unions [ ./humble_integrator ./recipes ./tests ];
            };
            nativeBuildInputs = [ pkgs.makeWrapper ];
            nativeCheckInputs = [ pkgs.python3 ];
            doCheck = true;
            checkPhase = ''
              runHook preCheck
              export HOME=$TMPDIR
              for t in tests/test_*.py; do python3 -I "$t"; done
              python3 -I humble_integrator/recipes.py check
              runHook postCheck
            '';
            installPhase = ''
              runHook preInstall
              mkdir -p $out/share/humble-integrator $out/bin
              cp -r humble_integrator recipes $out/share/humble-integrator/
              for tool in census recipes humble drive steam install; do
                makeWrapper ${pkgs.python3}/bin/python3 $out/bin/hi-$tool \
                  --add-flags "-I $out/share/humble-integrator/humble_integrator/$tool.py" \
                  --prefix PATH : ${lib.makeBinPath runtimePath}
              done
              makeWrapper ${pkgs.python3}/bin/python3 $out/bin/hi \
                --add-flags "-I $out/share/humble-integrator/humble_integrator/cli.py" \
                --prefix PATH : ${lib.makeBinPath runtimePath}
              runHook postInstall
            '';
            meta.mainProgram = "hi";
          };
        });

      apps = forAll (pkgs: {
        default = {
          type = "app";
          program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/hi";
        };
      });
    };
}
