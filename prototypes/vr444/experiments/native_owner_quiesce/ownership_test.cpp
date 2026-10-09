// Source-derived application-method harness. No Vulkan/OpenXR/device execution.
#include <atomic>
#include <cassert>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
struct scene;
struct application {
    std::mutex scene_stack_lock;
    std::vector<std::shared_ptr<scene>> scene_stack;
    static application& instance() { static application app; return app; }
    static void pop_scene();
    void cleanup();
};
static auto owner = std::this_thread::get_id();
static bool device_alive = true;
static unsigned prepared = 0, destroyed = 0;
struct scene : std::enable_shared_from_this<scene> {
    std::thread worker;
    std::atomic<bool> exit{false};
    std::promise<void> started;
    std::once_flag once;
    bool done = false;
    void start() {
        auto weak = weak_from_this();
        worker = std::thread([weak] {
            auto held = weak.lock(); assert(held);
            held->started.set_value();
            while (!held->exit.load()) std::this_thread::yield();
        });
        started.get_future().wait();
    }
    virtual void prepare_exit() noexcept {
        std::call_once(once,[this] {
            assert(std::this_thread::get_id()==owner && device_alive);
            auto& mutex=application::instance().scene_stack_lock;
            // The hook must be callable without the stack lock, even though a
            // callback might temporarily retain the final non-owner reference.
            assert(mutex.try_lock()); mutex.unlock();
            assert(weak_from_this().use_count() >= 2);
            exit.store(true); worker.join(); done=true; ++prepared;
        });
    }
    virtual ~scene() {
        assert(std::this_thread::get_id()==owner && device_alive && done);
        assert(!worker.joinable()); ++destroyed;
    }
};
// ACTUAL_METHODS are inserted below without rewriting their bodies.
@@METHODS@@
int main() {
    auto& app=application::instance();
    application::pop_scene(); app.cleanup();
    for (unsigned i=0;i<100;++i) {
        auto item=std::make_shared<scene>();item->start();
        app.scene_stack.push_back(item); item.reset();
        application::pop_scene();
        assert(prepared==i+1 && destroyed==i+1 && app.scene_stack.empty());
    }
    // A currently rendering owner reference must keep the popped scene alive;
    // quiescence still completes before pop returns, and repeats are harmless.
    auto render_ref=std::make_shared<scene>();render_ref->start();
    app.scene_stack.push_back(render_ref);application::pop_scene();
    assert(prepared==101 && destroyed==100 && render_ref->done);
    render_ref->prepare_exit();render_ref.reset();assert(destroyed==101);
    for (unsigned i=0;i<4;++i) {
        auto item=std::make_shared<scene>();item->start();app.scene_stack.push_back(item);
    }
    app.cleanup();assert(prepared==105 && destroyed==105 && app.scene_stack.empty());
    app.cleanup();application::pop_scene();device_alive=false;
    std::cout << "owner_retirement: 105 scenes, callback references joined, stack unlocked, device alive\n";
}
