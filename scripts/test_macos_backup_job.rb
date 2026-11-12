#!/usr/bin/env ruby
# Offline launchd/backup_job contract tests. No launchctl command is executed.
require 'digest'
require 'json'
require 'minitest/autorun'
require_relative '../collectors/macos/collector'

class MacCollectorBackupJobTests < Minitest::Test
  Status = Struct.new(:exitstatus) do
    def success?
      exitstatus.zero?
    end
  end

  def setup
    @now = Time.utc(2026, 11, 12, 8, 0, 0)
    @label = 'com.dongli.restic.home-compute-1'
    @plist = '/Library/LaunchDaemons/com.dongli.restic.home-compute-1.plist'
    @receipt = '/Library/ResticStatus/health.json'
    @plan = {
      'Label' => @label,
      'ProgramArguments' => ['/usr/bin/ruby', '/Library/Restic/lib/restic-mac.rb',
                             '--config', '/Library/Restic/config/home-compute-1.json', 'backup'],
      'WorkingDirectory' => '/Library/Restic',
      'StartCalendarInterval' => {'Hour' => 4, 'Minute' => 0},
      'RunAtLoad' => false,
      'UserName' => 'root',
      'GroupName' => 'wheel'
    }
    plist_json = JSON.generate(@plan)
    @job = {
      'scheduler' => 'launchd',
      'label' => @label,
      'plist' => @plist,
      'plist_sha256' => Digest::SHA256.hexdigest(plist_json),
      'receipt' => @receipt,
      'transport_receipt' => '/Library/ResticStatus/transport/home-compute-1.json',
      'max_age_seconds' => 93_600
    }
    @plist_json = plist_json
    @service_state = 'not running'
    @last_exit = '0'
    @disabled = false
    @receipt_value = {
      'schema' => 1,
      'profile' => 'home-compute-1',
      'status' => 'success',
      'last_success' => (@now - 3600).iso8601,
      'last_check' => (@now - 3600).iso8601,
      'snapshot_id' => 'a' * 64,
      'check_mode' => 'read-data',
      'attempt_started_utc' => (@now - 3700).iso8601,
      'attempt_finished_utc' => (@now - 3600).iso8601,
      'error_code' => nil
    }
    @transport_value = {
      'schema' => 1,
      'profile' => 'home-compute-1',
      'status' => 'up',
      'checked_utc' => (@now - 30).iso8601,
      'unauthenticated_status' => 401,
      'authenticated_config_status' => 'pinned',
      'error_code' => nil
    }
  end

  def run(*args)
    case [args[0], args[1]]
    when ['/bin/launchctl', 'print']
      [<<~OUTPUT, '', Status.new(0)]
        system/#{@label} = {
          path = #{@plist}
          state = #{@service_state}
          last exit code = #{@last_exit}
        }
      OUTPUT
    when ['/bin/launchctl', 'print-disabled']
      override = @disabled ? %(  "#{@label}" => disabled\n) : ''
      ["disabled services = {\n#{override}}\n", '', Status.new(0)]
    when ['/usr/bin/plutil', '-convert']
      [@plist_json, '', Status.new(0)]
    else
      [nil, nil, Status.new(1)]
    end
  end

  def controlled_public_file(path)
    return @plist_json if path == @plist
    return JSON.generate(@receipt_value) if path == @receipt
    return JSON.generate(@transport_value) if path == @job['transport_receipt']
    raise 'UNCONTROLLED_FILE'
  end

  def state(job = @job)
    backup_state(job, 'home-compute-1', @now)
  end

  def test_success_is_up_only_with_full_read_data_evidence
    assert_equal 'up', state
    @receipt_value['check_mode'] = 'metadata'
    assert_equal 'down', state
    @receipt_value['check_mode'] = 'read-data'
    @receipt_value['snapshot_id'] = 'a' * 63
    assert_equal 'unknown', state
    @receipt_value['snapshot_id'] = 'A' * 64
    assert_equal 'unknown', state
  end

  def test_failed_is_down
    @receipt_value['status'] = 'failed'
    @receipt_value['error_code'] = 'BACKUP_FAILED'
    assert_equal 'down', state
    @disabled = true
    assert_equal 'down', state
  end

  def test_running_preserves_a_fresh_prior_success_as_maintenance
    @receipt_value['status'] = 'running'
    @receipt_value['attempt_started_utc'] = (@now - 60).iso8601
    @receipt_value['attempt_finished_utc'] = nil
    assert_equal 'maintenance', state
    @last_exit = '1'
    @service_state = 'running'
    assert_equal 'maintenance', state
  end

  def test_running_receipt_emits_the_prior_checked_snapshot
    @receipt_value['status'] = 'running'
    @receipt_value['attempt_started_utc'] = (@now - 60).iso8601
    @receipt_value['attempt_finished_utc'] = nil
    metadata = backup_metadata(@job, 'home-compute-1', @now)
    assert_equal 'read-data', metadata['check_mode']
    assert_equal 'a' * 64, metadata['snapshot_id']
    assert_equal (@now - 3600).to_f, metadata['checked_at']
    assert_equal 'up', metadata['transport']['state']
  end

  def test_stale_receipt_is_down
    @receipt_value['last_success'] = (@now - 93_601).iso8601
    @receipt_value['last_check'] = @receipt_value['last_success']
    @receipt_value['attempt_started_utc'] = (@now - 93_700).iso8601
    @receipt_value['attempt_finished_utc'] = @receipt_value['last_success']
    assert_equal 'down', state
  end

  def test_missing_or_malformed_receipt_is_unknown
    @receipt_value = nil
    assert_equal 'unknown', state
    @receipt_value = {
      'schema' => 1,
      'profile' => 'other-host',
      'status' => 'success'
    }
    assert_equal 'unknown', state
  end

  def test_pending_or_unknown_launchd_failure_is_unknown
    @receipt_value['status'] = 'pending'
    assert_equal 'unknown', state
    @receipt_value['status'] = 'success'
    @plan['Label'] = 'com.example.changed'
    assert_equal 'down', state
  end

  def test_unknown_or_colliding_job_fields_are_rejected
    assert_equal 'unknown', state(@job.merge('command' => 'do-not-run'))
    assert_equal 'unknown', state(@job.merge('receipt' => @plist))
    assert_equal 'unknown', state(@job.merge('plist' => '/Library/../private'))
    assert_equal 'unknown', state(@job.merge('plist_sha256' => 'A' * 64))
    assert_equal 'unknown', state(@job.merge('receipt' => "/Library/Application Support/Example/\u0001health.json"))
    assert_equal 'unknown', state(@job.merge('max_age_seconds' => 59))
  end

  def test_only_the_canonical_ruby_backup_argument_vector_is_accepted
    canonical = ['/usr/bin/ruby', '-E', 'UTF-8',
                 '/Library/Restic/lib/restic-mac.rb', '--config',
                 '/Library/Restic/config/home-compute-1.json', 'backup']
    @plan['ProgramArguments'] = canonical
    @plist_json = JSON.generate(@plan)
    @job['plist_sha256'] = Digest::SHA256.hexdigest(@plist_json)
    assert_equal 'up', state

    @plan['ProgramArguments'] = [*canonical, '--unexpected']
    @plist_json = JSON.generate(@plan)
    @job['plist_sha256'] = Digest::SHA256.hexdigest(@plist_json)
    assert_equal 'down', state

    @plan['ProgramArguments'] = ['/bin/sh', '-c', 'backup']
    @plist_json = JSON.generate(@plan)
    @job['plist_sha256'] = Digest::SHA256.hexdigest(@plist_json)
    assert_equal 'down', state
  end

  def test_metadata_omits_paths_and_raw_receipt_content
    metadata = backup_metadata(@job, 'home-compute-1', @now)
    assert_equal %w[checked_at check_mode schema_version snapshot_id transport].sort,
                 metadata.keys.sort
    serialized = JSON.generate(metadata)
    refute_includes serialized, '/Library'
    refute_includes serialized, 'error_code'
    refute_includes serialized, 'attempt_started'
  end

  def test_transport_receipt_expires_at_fifteen_minutes
    @transport_value['checked_utc'] = (@now - 900).iso8601
    assert_equal 'up', transport_metadata(
      @job['transport_receipt'], 'home-compute-1', @now)['state']
    @transport_value['checked_utc'] = (@now - 901).iso8601
    metadata = transport_metadata(@job['transport_receipt'], 'home-compute-1', @now)
    assert_equal 'down', metadata['state']
    assert_equal 'stale', metadata['error_code']
    @transport_value['status'] = 'unknown'
    @transport_value['unauthenticated_status'] = nil
    @transport_value['authenticated_config_status'] = nil
    assert_equal 'down', transport_metadata(
      @job['transport_receipt'], 'home-compute-1', @now)['state']
  end

  def test_invalid_configured_transport_is_reported_as_unknown
    @receipt_value['snapshot_id'] = 'private/path'
    assert_nil backup_metadata(@job, 'home-compute-1', @now)
    @receipt_value['snapshot_id'] = 'a' * 64
    @transport_value['error_code'] = '/private/path'
    metadata = backup_metadata(@job, 'home-compute-1', @now)
    assert_equal 'unknown', metadata['transport']['state']
    assert_equal 'receipt_invalid', metadata['transport']['error_code']
    assert_equal @now.to_f, metadata['transport']['checked_at']
    refute_includes JSON.generate(metadata), '/private'
  end
end
