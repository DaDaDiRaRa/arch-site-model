# 확장(arch_site_model) 생성 흐름 무인 검증 — SketchUp.exe -RubyStartup 으로 실행.
# 다이얼로그 "모델 생성" 버튼이 하는 일(ApiClient.generate → 정사영상 다운로드 → Builder.build)을
# 그대로 호출해 운영 백엔드로 모델을 조립하고, 층고·도시계획·태그·그룹을 보고서로 남긴 뒤 종료한다.
require "json"
DIR = File.dirname(__FILE__)
REPORT = File.join(DIR, "ext_report.json")
FLOOR_H = 3.2 # 기본값(3.5)과 다르게 줘서 층고가 실제로 전달되는지 본다
$out = { started: Time.now.to_s, su_version: Sketchup.version }

def stage(name)
  ($out[:stages] ||= []) << "#{Time.now.strftime('%H:%M:%S')} #{name}"
  File.write(REPORT + ".progress", JSON.pretty_generate($out))
end
stage("loaded")

def finish
  $out[:finished] = Time.now.to_s
  File.write(REPORT, JSON.pretty_generate($out))
  begin
    Sketchup.active_model.save(File.join(DIR, "ext_result.skp"))
  rescue StandardError
    nil
  end
  UI.start_timer(1, false) { Sketchup.quit }
end

def report(model)
  root = model.entities.grep(Sketchup::Group).find { |g| g.name == "arch-site-model" }
  $out[:root_group] = !root.nil?
  return unless root
  names = Hash.new(0)
  root.entities.grep(Sketchup::Group).each { |g| names[g.name.empty? ? "(이름 없음)" : g.name] += 1 }
  $out[:child_groups] = names.sort_by { |_, v| -v }.first(15).to_h
  $out[:tags] = model.layers.map(&:name).grep(/building|terrain|road|lane|cadastral|도시계획|sidewalk/)
  plan = root.entities.grep(Sketchup::Group).find { |g| g.name == "도시계획" }
  $out[:planning_subgroups] = plan ? plan.entities.grep(Sketchup::Group).map { |g| "#{g.name}(#{g.entities.grep(Sketchup::Edge).length}선)" } : nil
  $out[:ortho_material] = model.materials.select { |m| m.texture }.map { |m| m.name }
  bb = root.bounds
  $out[:size_m] = [bb.width, bb.height, bb.depth].map { |v| v.to_m.round(1) }
  model.active_view.zoom_extents
  model.active_view.write_image(filename: File.join(DIR, "ext_shot.png"), width: 1600, height: 1000, antialias: true)
end

UI.start_timer(4, false) do
  begin
    unless defined?(ArchSiteModel::ApiClient) && defined?(ArchSiteModel::Builder)
      $out[:error] = "확장이 로드되지 않았습니다"
      finish
      next
    end
    stage("timer")
    $out[:backend] = ArchSiteModel::Settings.backend_url
    params = {
      "address" => "대전광역시 서구 괴정동 358", "radius_m" => 150, "floor_height_m" => FLOOR_H,
      "terrain" => true, "orthophoto" => true, "roads" => true, "planning" => true, "qa" => true,
    }
    t0 = Time.now
    stage("request")
    ArchSiteModel::ApiClient.generate(ArchSiteModel::Settings.backend_url, params) do |result|
      begin
        stage("api-callback")
        $out[:api_sec] = (Time.now - t0).round(1)
        if result[:error]
          $out[:error] = result[:error]
          finish
          next
        end
        geom = result[:geometry]
        bs = geom["buildings"] || []
        with = bs.select { |b| b["floors"] }
        $out[:buildings] = bs.length
        $out[:floor_height_ok] = with.all? { |b| (b["height"] - b["floors"] * FLOOR_H).abs < 0.01 }
        $out[:sample_building] = with.first && { floors: with.first["floors"], height: with.first["height"] }
        $out[:planning_lines] = (geom["planning"] || []).length
        build = lambda do |png, ext|
          begin
            stage("build")
            $out[:built] = ArchSiteModel::Builder.build(geom, result[:warnings], png, ext, result[:qa])
            report(Sketchup.active_model)
          rescue Exception => e
            $out[:error] = "조립 실패: #{e.class}: #{e.message} #{e.backtrace.first(3)}"
          ensure
            finish
          end
        end
        ortho = result[:ortho]
        if ortho && ortho[:url]
          ArchSiteModel::ApiClient.download_binary("#{ArchSiteModel::Settings.backend_url}#{ortho[:url]}") do |bytes|
            $out[:ortho_bytes] = bytes ? bytes.bytesize : nil
            png = bytes ? ArchSiteModel::Main.write_temp_png(bytes) : nil
            build.call(png, ortho[:extent])
          end
        else
          build.call(nil, nil)
        end
      rescue Exception => e
        $out[:error] = "#{e.class}: #{e.message}"
        finish
      end
    end
  rescue Exception => e
    $out[:error] = "#{e.class}: #{e.message}"
    finish
  end
end
