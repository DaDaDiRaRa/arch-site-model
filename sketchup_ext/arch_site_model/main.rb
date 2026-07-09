# 확장 진입점: 메뉴 등록 + 입력 다이얼로그 + 백엔드 호출 → 조립 오케스트레이션.

require "sketchup.rb"
require "json"

module ArchSiteModel; end

require_relative "settings"
require_relative "api_client"
require_relative "builder"

module ArchSiteModel
  module Main
    DIALOG_HTML = File.join(File.dirname(__FILE__), "dialog.html").freeze

    def self.show_dialog
      @dialog = create_dialog unless @dialog && @dialog.respond_to?(:visible?)
      if @dialog.visible?
        @dialog.bring_to_front
      else
        @dialog.show
      end
    end

    def self.create_dialog
      dlg = UI::HtmlDialog.new(
        dialog_title: "대지모델 생성",
        preferences_key: "arch_site_model_dialog",
        scrollable: true,
        resizable: true,
        width: 440,
        height: 480,
        style: UI::HtmlDialog::STYLE_DIALOG,
      )
      dlg.set_file(DIALOG_HTML)
      dlg.add_action_callback("generate") { |_ctx, payload| handle_generate(dlg, payload) }
      dlg.add_action_callback("cancel") { |_ctx, _p| @cancel = true }
      dlg
    end

    # 이 반경(m) 초과면 타일 순차조립(대반경). 이하는 단일 조립.
    TILE_THRESHOLD_M = 500

    def self.handle_generate(dlg, payload)
      params = JSON.parse(payload)
      if params["address"].to_s.strip.empty?
        dlg.execute_script("window.showError(#{JSON.generate('주소를 입력하세요.')});")
        return
      end
      @cancel = false
      radius = (params["radius_m"] || 250).to_i
      if radius > TILE_THRESHOLD_M
        start_tiled(dlg, params)
      else
        start_single(dlg, params)
      end
    rescue StandardError => e
      dlg.execute_script("window.showError(#{JSON.generate("오류: #{e.message}")});")
    end

    # 단일 조립(소반경): /api/generate 1회 → (정사영상 다운로드) → 전체 조립.
    def self.start_single(dlg, params)
      dlg.execute_script("window.showBusy();")
      ApiClient.generate(Settings.backend_url, params) do |result|
        if result[:error]
          dlg.execute_script("window.showError(#{JSON.generate(result[:error])});")
          next
        end
        ortho = result[:ortho]
        puts "[ortho] backend: #{ortho ? "extent=#{ortho[:extent].inspect} url=#{ortho[:url]}" : 'nil (정사영상 미요청 또는 백엔드 미생성)'}"
        if ortho && ortho[:url]
          full = "#{Settings.backend_url}#{ortho[:url]}"
          ApiClient.download_binary(full) do |bytes|
            puts "[ortho] 다운로드: #{bytes ? "#{bytes.bytesize}B" : 'nil(실패)'}"
            png = bytes ? write_temp_png(bytes) : nil
            puts "[ortho] temp png: #{png || 'nil'}"
            finish_single(dlg, result, png, ortho[:extent])
          end
        else
          finish_single(dlg, result, nil, nil)
        end
      end
    end

    def self.finish_single(dlg, result, ortho_png, ortho_extent)
      qa = result[:qa]
      n = Builder.build(result[:geometry], result[:warnings], ortho_png, ortho_extent, qa)
      done = { "count" => n, "warnings" => result[:warnings] || [] }
      if qa && qa["summary"]
        done["qa"] = { "total" => qa["summary"]["total"], "warnings" => qa["summary"]["warnings"] }
      end
      dlg.execute_script("window.showDone(#{JSON.generate(done)});")
    rescue StandardError => e
      dlg.execute_script("window.showError(#{JSON.generate("조립 실패: #{e.message}")});")
    end

    # 정사영상 바이트 → 임시 PNG 파일 경로(머티리얼 텍스처용). 실패 시 nil.
    def self.write_temp_png(bytes)
      path = File.join(Sketchup.temp_dir, "asm_ortho_#{bytes.hash & 0xffffff}.png")
      File.open(path, "wb") { |f| f.write(bytes) }
      path
    rescue StandardError
      nil
    end

    # 대반경 타일 순차조립: 계획 → 타일별 fetch+조립(진행바/취소).
    def self.start_tiled(dlg, params)
      dlg.execute_script("window.showBusy();")
      ApiClient.tile_plan(Settings.backend_url, params) do |result|
        if result[:error]
          dlg.execute_script("window.showError(#{JSON.generate(result[:error])});")
          next
        end
        plan = result[:plan]
        tiles = plan["tiles"] || []
        if tiles.empty?
          dlg.execute_script("window.showError(#{JSON.generate('생성할 타일이 없습니다.')});")
          next
        end
        model = Sketchup.active_model
        layers = {
          "buildings"  => true,
          "terrain"    => params["terrain"] != false,
          "orthophoto" => params["orthophoto"] == true, # 타일별 풀해상도 정사영상
          "roads"      => params["roads"] == true,       # 타일별 도로·보도·차선 (Phase R)
          "water"      => params["water"] == true,        # 타일별 수계 (수면)
        }
        root_holder = { group: nil } # root는 첫 타일과 함께 생성(빈 그룹 purge 방지)
        state = { total: tiles.length, built: 0, errors: 0 }
        build_next_tile(dlg, model, root_holder, plan, tiles, layers, 0, state)
      end
    end

    # base64 정사영상 → 임시 PNG 파일 경로. 실패 시 nil.
    def self.write_b64_png(b64)
      require "base64"
      path = File.join(Sketchup.temp_dir, "asm_tiled_ortho_#{b64.hash & 0xffffff}.png")
      File.open(path, "wb") { |f| f.write(Base64.decode64(b64)) }
      path
    rescue StandardError
      nil
    end

    # 타일 하나 fetch+조립 후 다음으로 재귀(Sketchup::Http 콜백 체이닝 = 타일 사이
    # UI 갱신·취소 확인이 자연히 가능). 취소되거나 끝나면 finalize.
    def self.build_next_tile(dlg, model, root_holder, plan, tiles, layers, idx, state)
      if @cancel || idx >= tiles.length
        finalize_tiled(dlg, model, idx, state)
        return
      end
      tile = tiles[idx]
      progress = { "i" => idx + 1, "n" => state[:total], "buildings" => state[:built] }
      dlg.execute_script("window.showTileProgress(#{JSON.generate(progress)});")
      req = {
        "bbox_4326"     => tile["bbox_4326"],
        "bbox_5186"     => tile["bbox_5186"],
        "origin_offset" => plan["origin_offset"],
        "layers"        => layers,
      }
      ApiClient.generate_tile(Settings.backend_url, req) do |result|
        if result[:error]
          state[:errors] += 1 # 한 타일 실패는 건너뛰고 계속
          state[:first_error] ||= result[:error]
          log_tile_error(tile["tile_id"], result[:error])
        else
          begin
            # 타일별 정사영상(있으면) → temp PNG → 이 타일 지형에 드레이프.
            o = result[:ortho]
            opng = o && o["image_b64"] ? write_b64_png(o["image_b64"]) : nil
            oext = o ? o["extent_local_m"] : nil
            n = Builder.build_tile(model, root_holder, result[:geometry], tile["tile_id"],
                                   opng, oext)
            state[:built] += n
          rescue StandardError => e
            state[:errors] += 1
            state[:first_error] ||= "조립: #{e.message}"
            log_tile_error(tile["tile_id"], "조립 #{e.message}")
          end
        end
        build_next_tile(dlg, model, root_holder, plan, tiles, layers, idx + 1, state)
      end
    end

    # 타일 오류를 Ruby Console에 남긴다(Window ▸ Ruby Console에서 확인).
    def self.log_tile_error(tile_id, msg)
      puts "[arch-site-model] 타일 #{tile_id} 실패: #{msg}"
    rescue StandardError
      nil
    end

    def self.finalize_tiled(dlg, model, done_idx, state)
      begin
        model.active_view.zoom_extents
      rescue StandardError
        nil
      end
      Builder.hide_profiles(model) # 타일 지형 그룹 외곽선(검은 선) 제거
      warns = []
      warns << "첫 실패 사유: #{state[:first_error]}" if state[:first_error]
      done = {
        "count"       => state[:built],
        "warnings"    => warns,
        "cancelled"   => @cancel,
        "errors"      => state[:errors],
        "tiles_done"  => done_idx,
        "tiles_total" => state[:total],
        "first_error" => state[:first_error],
      }
      dlg.execute_script("window.showDone(#{JSON.generate(done)});")
    end

    unless file_loaded?(__FILE__)
      cmd = UI::Command.new("대지모델 생성…") { show_dialog }
      cmd.tooltip = "주소로 주변 지형·건물 대지모델 생성"
      cmd.status_bar_text = "주소를 입력하면 백엔드에서 지형·건물을 받아 조립합니다."
      UI.menu("Extensions").add_item(cmd)
      file_loaded(__FILE__)
    end
  end
end
