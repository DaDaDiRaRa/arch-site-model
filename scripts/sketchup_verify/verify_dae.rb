# arch-site-model .dae 자동 검증 — SketchUp.exe -RubyStartup 로 실행.
# 가져오기 → (확장의 자동 모서리 정리가 돌 시간) → 통계·스크린샷 → 저장 → 종료.
require "json"
DIR = File.dirname(__FILE__)
DAE = File.join(DIR, "pkg", "site.dae")
REPORT = File.join(DIR, "report.json")
$out = { started: Time.now.to_s, su_version: Sketchup.version }

def finish
  $out[:finished] = Time.now.to_s
  File.write(REPORT, JSON.pretty_generate($out))
  UI.start_timer(1, false) { Sketchup.quit }
end

def measure(model)
  $out[:auto_runs] = defined?(ArchSiteModel::ImportSoftener) ? [ArchSiteModel::ImportSoftener.runs, ArchSiteModel::ImportSoftener.last_result] : "no-ext"
  # 자동 정리가 이미 돌았으면 여기서 0을 돌려준다(= 관찰자 동작 확인)
  $out[:softened_by_manual_call] = defined?(ArchSiteModel::ImportSoftener) ? ArchSiteModel::ImportSoftener.soften_all(model) : "no-ext"
  soft = 0; hard = 0
  model.definitions.each do |d|
    f = d.entities.grep(Sketchup::Face).first
    next unless f && f.material && f.material.name.start_with?("정사영상", "지형")
    d.entities.grep(Sketchup::Edge) { |e| e.soft? ? soft += 1 : hard += 1 }
  end
  $out[:terrain_edges] = { soft: soft, hard: hard }
  # 재질별 [정의 수, soft 모서리, hard 모서리] — 건물은 soft가 0이어야 한다(각진 매스)
  bym = Hash.new { |h, k| h[k] = [0, 0, 0] }
  model.definitions.each do |d|
    f = d.entities.grep(Sketchup::Face).first
    next unless f
    k = f.material ? f.material.name : "(면 재질 없음)"
    bym[k][0] += 1
    d.entities.grep(Sketchup::Edge) { |e| e.soft? ? bym[k][1] += 1 : bym[k][2] += 1 }
  end
  $out[:edges_by_material] = bym
  bb = model.bounds
  $out[:size_m] = [bb.width, bb.height, bb.depth].map { |v| v.to_m.round(1) }
  $out[:z_min_m] = bb.min.z.to_m.round(1)
  $out[:materials] = model.materials.map(&:name).reject { |n| n.start_with?("Sree") }
  $out[:ortho_texture] = model.materials.select { |m| m.texture }.map { |m| [m.name, File.basename(m.texture.filename), m.texture.image_width] }
  view = model.active_view
  view.camera = Sketchup::Camera.new(bb.center.offset([bb.width * -0.6, bb.height * -0.9, bb.width * 0.55]), bb.center, Z_AXIS)
  view.zoom_extents
  view.write_image(filename: File.join(DIR, "shot_iso.png"), width: 1600, height: 1000, antialias: true)
  view.camera = Sketchup::Camera.new(bb.center.offset(Z_AXIS, bb.width * 2), bb.center, Y_AXIS)
  view.zoom_extents
  view.write_image(filename: File.join(DIR, "shot_top.png"), width: 1600, height: 1000, antialias: true)
  model.save(File.join(DIR, "verify_result.skp")) # 저장해 두면 종료 시 저장 여부를 묻지 않는다
end

UI.start_timer(4, false) do
  begin
    model = Sketchup.active_model
    # 기본 템플릿의 사람 모형 제거(치수·재질 통계 오염 방지)
    model.entities.to_a.each { |e| e.erase! if e.valid? }
    $out[:import_ok] = model.import(DAE, show_summary: false)
    model.select_tool(nil)
    if model.entities.length == 0
      # 실제 사용처럼 가져온 최상위 정의를 원점에 놓는다(사용자가 배치 도구로 클릭하는 것과 같은 결과)
      root = model.definitions.to_a
                  .reject { |d| d.group? || d.image? || d.name.start_with?("Sree") || !d.instances.empty? }
                  .max_by { |d| d.entities.length }
      model.entities.add_instance(root, Geom::Transformation.new) if root
      $out[:placed_manually] = root ? root.name : nil
    end
    UI.start_timer(3, false) do
      begin
        measure(model)
      rescue Exception => e
        $out[:error] = "#{e.class}: #{e.message} #{e.backtrace.first(3)}"
      ensure
        finish
      end
    end
  rescue Exception => e
    $out[:error] = "#{e.class}: #{e.message} #{e.backtrace.first(3)}"
    finish
  end
end
