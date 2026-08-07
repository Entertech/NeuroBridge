#ifndef AC_ATTENTION_HEADER_FILE_GUARD
#define AC_ATTENTION_HEADER_FILE_GUARD
#include "TypeDefine.h"
#include "SessionCache.h"
#include "SimpleHandler.h"
namespace ac
{
    struct AttentionTemp
    {
        int index;
        nc::NdArray<double> wearFlagStore;
        basic::affection::handler::AttentionHandlerTemp attentionTmp;
        vectord attentionRec;
    };

    struct AttentionReportRes
    {
        vectord attentionRec;
        double attentionAvg;
    };

    class AttentionComputing
    {
    public:
        double trigger(const basic::SessionCache &cache);
        AttentionReportRes report();
        AttentionComputing();
        ~AttentionComputing();

    private:
        AttentionTemp temp;
    };

}

#endif