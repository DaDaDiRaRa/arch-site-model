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
      dlg
    end

    def self.handle_generate(dlg, payload)
      params = JSON.parse(payload)
      if params["address"].to_s.strip.empty?
        dlg.execute_script("window.showError(#{JSON.generate('주소를 입력하세요.')});")
        return
      end

      dlg.execute_script("window.showBusy();")
      ApiClient.generate(Settings.backend_url, params) do |result|
        if result[:error]
          dlg.execute_script("window.showError(#{JSON.generate(result[:error])});")
        else
          begin
            n = Builder.build(result[:geometry], result[:warnings])
            done = { "count" => n, "warnings" => result[:warnings] || [] }
            dlg.execute_script("window.showDone(#{JSON.generate(done)});")
          rescue StandardError => e
            dlg.execute_script("window.showError(#{JSON.generate("조립 실패: #{e.message}")});")
          end
        end
      end
    rescue StandardError => e
      dlg.execute_script("window.showError(#{JSON.generate("오류: #{e.message}")});")
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
