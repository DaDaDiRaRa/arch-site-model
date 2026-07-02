# arch-site-model — SketchUp 확장 로더 (Phase B, B1: 지형+건물)
#
# 주소를 입력하면 백엔드(/api/generate)를 호출해 받은 geometry JSON으로
# 데스크톱 SketchUp 안에 지형(mesh)과 건물(돌출)을 조립한다.
#
# 설치: SketchUp > 확장 관리자(Extension Manager) > 설치 > arch_site_model.rbz
# 대상: SketchUp 2021+ (HtmlDialog, Sketchup::Http, Geom::PolygonMesh)

require "sketchup.rb"
require "extensions.rb"

module ArchSiteModel
  PLUGIN_ID = "arch_site_model".freeze
  PLUGIN_DIR = File.join(File.dirname(__FILE__), PLUGIN_ID).freeze

  unless defined?(@extension_loaded) && @extension_loaded
    ext = SketchupExtension.new(
      "arch-site-model",
      File.join(PLUGIN_DIR, "main"),
    )
    ext.description = "주소로 주변 지형·건물 3D 대지모델을 생성 (백엔드 연동)"
    ext.version = "0.1.0"
    ext.creator = "arch-site-model"
    ext.copyright = "2026"
    Sketchup.register_extension(ext, true)
    @extension_loaded = true
  end
end
